"""Exercise drive export with real browser file streams in isolated OPFS storage.

Only the native picker and selected permission/failure cases are simulated.
No server-mounted drive or actual USB device is used. Called by browser_documents.
"""
import base64
from pathlib import Path

from playwright.sync_api import expect


SETUP = """(() => {
  window.driveMode = 'ok';
  window.drivePickers = 0;
  window.driveSavePickers = 0;
  window.driveSaveName = 'chosen-print.pdf';
  window.driveWrites = 0;
  window.drivePermissionClicks = 0;
  window.driveAborts = 0;
  const root = () => navigator.storage.getDirectory();
  window.testDrive = async () => (await root()).getDirectoryHandle('Test USB', {create:true});
  window.showDirectoryPicker = async options => {
    if (!navigator.userActivation.isActive || options.mode !== 'readwrite') throw Error('Picker lost user activation');
    window.drivePickers++;
    if (['cancel','blocked'].includes(window.driveMode)) throw new DOMException(window.driveMode === 'cancel' ? 'Cancelled' : 'Permission blocked after selection', 'AbortError');
    return window.testDrive();
  };
  window.showSaveFilePicker = async options => {
    if (!navigator.userActivation.isActive) throw Error('Save picker lost user activation');
    if (!options.suggestedName.endsWith('.pdf') || options.types[0].accept['application/pdf'][0] !== '.pdf') throw Error('Save picker missing PDF type');
    window.driveSavePickers++;
    if (window.driveMode === 'save-cancel') throw new DOMException('No file selected', 'AbortError');
    if (window.driveMode === 'save-denied') throw new DOMException('File permission denied', 'NotAllowedError');
    return (await window.testDrive()).getFileHandle(window.driveSaveName, {create:true});
  };
  const handles = FileSystemHandle.prototype;
  const query = handles.queryPermission;
  const permission = handles.requestPermission;
  handles.queryPermission = function(options) {
    return ['denied','prompt'].includes(window.driveMode) ? Promise.resolve('prompt') : query.call(this,options);
  };
  handles.requestPermission = function(options) {
    if (!navigator.userActivation.isActive) throw Error('Permission request lost user activation');
    window.drivePermissionClicks++;
    return ['denied','prompt'].includes(window.driveMode) ? Promise.resolve(window.driveMode === 'denied' ? 'denied' : 'granted') : permission.call(this,options);
  };
  const getFile = FileSystemDirectoryHandle.prototype.getFileHandle;
  FileSystemDirectoryHandle.prototype.getFileHandle = function(...args) {
    if (window.driveMode === 'disconnected') throw new DOMException('Disconnected', 'NotFoundError');
    return getFile.apply(this,args);
  };
  const createWritable = FileSystemFileHandle.prototype.createWritable;
  FileSystemFileHandle.prototype.createWritable = async function(...args) {
    window.driveWrites++;
    const stream = await createWritable.apply(this,args);
    if (!['full','close-error'].includes(window.driveMode)) return stream;
    return {
      write: data => {
        if (window.driveMode === 'full') throw new DOMException('Full', 'QuotaExceededError');
        return stream.write(data);
      },
      close: () => { throw new DOMException('Drive removed while closing', 'NotReadableError'); },
      abort: async () => { window.driveAborts++; await stream.abort(); },
    };
  };
})();"""


def verify_drive_export(page, pdf):
    page.add_init_script(SETUP)
    page.evaluate(SETUP)
    batch = page.locator("#document-batch-detail a[href$='/download']").get_attribute("href").split("/")[-2]
    download_route = f"**/api/documents/batches/{batch}/download"

    def open_export():
        page.get_by_role("button", name="Export to external drive", exact=True).click()
        return page.locator("#document-drive-export")

    def back():
        page.get_by_role("button", name="Back to print job", exact=True).click()

    def reopen():
        # Reload, then open the job from the list the page shows it in: the print library's Print jobs tab, or the print jobs of
        # its assignment's Create area (ASSIGNMENT-WORKSPACE), where browser_documents opens it.
        page.reload()
        tab = page.get_by_role("tab", name="Print jobs", exact=True)
        row = page.locator(f'[data-batch="{batch}"]')
        expect(tab.or_(row).first).to_be_visible()
        if tab.count():
            tab.click()
        row.click()

    def files():
        return page.evaluate("""async () => {
          const result = {};
          for await (const [name,handle] of (await window.testDrive()).entries()) {
            const bytes = new Uint8Array(await (await handle.getFile()).arrayBuffer());
            let text = '';
            for (let i=0;i<bytes.length;i+=8192) text += String.fromCharCode(...bytes.subarray(i,i+8192));
            result[name] = btoa(text);
          }
          return result;
        }""")

    dialog = open_export()
    choose = dialog.get_by_role("button", name="Choose USB folder & export", exact=True)
    page.evaluate("window.driveMode = 'cancel'")
    choose.click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("did not grant access")
    expect(dialog.locator('[role="alert"]')).not_to_contain_text("Export cancelled")
    assert page.evaluate("window.driveSavePickers") == 0
    assert not files()
    page.evaluate("window.driveMode = 'ok'")
    choose.click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Exported and verified", timeout=15000)
    expect(dialog.get_by_role("button", name="Export verified", exact=True)).to_have_attribute("aria-busy", "false")
    expect(dialog.get_by_role("button", name="Export verified", exact=True)).to_have_css("cursor", "not-allowed")
    expected = base64.b64encode(pdf).decode()
    filename = f"print-{batch}.pdf"
    assert files() == {filename: expected}
    assert page.evaluate("window.drivePickers") == 2
    back()
    dialog = open_export()
    expect(dialog.locator('[role="status"]')).to_contain_text("Already saved and verified")
    assert page.evaluate("window.drivePickers") == 2
    assert files() == {filename: expected}

    # IndexedDB restores a real serializable directory handle after reload.
    reopen()
    dialog = open_export()
    expect(dialog.locator('[role="status"]')).to_contain_text("Already saved and verified")
    assert page.evaluate("window.drivePickers") == 0
    back()

    # A stale/altered HTTP body must be rejected before any destination writes.
    before = files()
    page.route(download_route, lambda route: route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-damaged"))
    dialog = open_export()
    expect(dialog.locator('[role="alert"]')).to_contain_text("failed verification")
    assert files() == before
    page.unroute(download_route)
    back()
    page.route(download_route, lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"The print file is temporarily unavailable."}'))
    dialog = open_export()
    expect(dialog.locator('[role="alert"]')).to_contain_text("The print file is temporarily unavailable")
    assert files() == before
    page.unroute(download_route)
    back()

    # Permission renewal requires a fresh click, and denial never saves a file.
    page.evaluate("window.driveMode = 'denied'")
    dialog = open_export()
    dialog.get_by_role("button", name="Allow access & export", exact=True).click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("access was not granted")
    assert files() == before
    page.evaluate("window.driveMode = 'prompt'")
    dialog.get_by_role("button", name="Allow access & export", exact=True).click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Already saved and verified")
    assert page.evaluate("window.drivePermissionClicks") == 2
    back()
    page.evaluate("window.driveMode = 'disconnected'")
    dialog = open_export()
    expect(dialog.locator('[role="alert"]')).to_contain_text("drive or folder is unavailable")
    page.evaluate("window.driveMode = 'ok'")
    assert files() == before
    back()

    # Preserve a different existing file instead of overwriting it.
    page.evaluate("""async name => {
      const f = await (await window.testDrive()).getFileHandle(name);
      const writer = await f.createWritable(); await writer.write('Keep this file'); await writer.close();
    }""", filename)
    dialog = open_export()
    expect(dialog.locator('[role="status"]')).to_contain_text("Exported and verified")
    saved = files()
    assert saved[filename] == base64.b64encode(b"Keep this file").decode()
    assert list(saved.values()).count(expected) == 1
    back()
    for mode, message in [("full", "free space"), ("close-error", "drive or folder is unavailable")]:
        page.evaluate("mode => window.driveMode = mode", mode)
        dialog = open_export()
        expect(dialog.locator('[role="alert"]')).to_contain_text(message)
        expect(dialog.locator('[role="status"]')).to_have_text("Export not completed.")
        expect(dialog.get_by_role("button", name="Export to this folder", exact=True)).to_be_enabled()
        assert list(files().values()).count(expected) == 1
        back()
    assert page.evaluate("window.driveAborts") == 2
    page.evaluate("window.driveMode = 'ok'")

    # Forgetting access changes only the browser preference, not drive contents.
    page.evaluate("window.driveMode = 'denied'")
    dialog = open_export()
    before = files()
    dialog.get_by_role("button", name="Forget folder", exact=True).click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Folder forgotten")
    assert files() == before
    reopen()
    dialog = open_export()
    expect(dialog.get_by_role("button", name="Choose USB folder & export", exact=True)).to_be_enabled()
    back()

    # A folder selection rejected by the browser must not be blamed on the user.
    page.evaluate("window.driveMode = 'blocked'")
    dialog = open_export()
    dialog.get_by_role("button", name="Choose USB folder & export", exact=True).click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("folder access blocked")
    assert page.evaluate("window.driveSavePickers") == 0
    before = files()
    save_as = dialog.get_by_role("button", name="Save PDF as…", exact=True)
    for mode, message in [("save-cancel", "No PDF destination was granted"), ("save-denied", "access was not granted")]:
        page.evaluate("mode => window.driveMode = mode", mode)
        save_as.click()
        expect(dialog.locator('[role="alert"]')).to_contain_text(message)
        assert files() == before
        expect(save_as).to_be_enabled()
    page.evaluate("window.driveMode = 'ok'")
    save_as.click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Exported and verified: chosen-print.pdf")
    expect(save_as).to_have_attribute("aria-busy", "false")
    expect(save_as).to_have_css("cursor", "pointer")
    expect(dialog.locator('#document-drive-destination')).to_have_text("No USB folder selected.")
    assert files() == {**before, "chosen-print.pdf": expected}
    save_as.click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Already saved and verified: chosen-print.pdf")

    # A bad source must never modify a selected existing file.
    before = files()
    writes = page.evaluate("window.driveWrites")
    page.route(download_route, lambda route: route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-damaged"))
    save_as.click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("failed verification")
    assert files() == before and page.evaluate("window.driveWrites") == writes
    page.unroute(download_route)
    page.evaluate("name => window.driveSaveName = name", filename)
    save_as.click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("Choose a new filename to preserve it")
    assert files() == before and page.evaluate("window.driveWrites") == writes

    page.evaluate("window.driveSaveName = 'interrupted.pdf'; window.driveMode = 'close-error'")
    save_as.click()
    expect(dialog.locator('[role="alert"]')).to_contain_text("drive or folder is unavailable")
    expect(dialog.locator('[role="status"]')).to_have_text("Export not completed.")
    assert files()["interrupted.pdf"] != expected
    page.evaluate("window.driveMode = 'ok'")
    for width in (1440, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Drive error overflow at {width}"
    back()

    # Saving one file does not remember its folder, including across reloads.
    reopen()
    dialog = open_export()
    expect(dialog.locator('#document-drive-destination')).to_have_text("No USB folder selected.")
    back()
    # File saving can work independently of directory access.
    page.evaluate("window.showDirectoryPicker = undefined")
    dialog = open_export()
    expect(dialog.get_by_role("button", name="Choose USB folder & export", exact=True)).to_have_count(0)
    dialog.get_by_role("button", name="Save PDF as…", exact=True).click()
    expect(dialog.locator('[role="status"]')).to_contain_text("Already saved and verified: chosen-print.pdf")
    back()

    # Unsupported browsers show a manual option, never a false drive success.
    page.evaluate("window.showSaveFilePicker = undefined")
    dialog = open_export()
    expect(dialog).to_contain_text("This browser cannot save directly")
    expect(dialog.locator('[role="status"]')).to_have_text("No file has been exported to a drive.")
    expect(dialog.get_by_role("link", name="Download PDF instead", exact=True)).to_be_visible()
    for width in (1440, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Drive export overflow at {width}"
    Path("test-results").mkdir(exist_ok=True)
    page.screenshot(path="test-results/document-drive-export.png", full_page=True)
    back()
    page.set_viewport_size({"width": 1440, "height": 1080})
