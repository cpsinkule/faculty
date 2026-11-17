"""Conditional team decisions, automatic finalization and retained model history in the UI."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_teams import GradingTeams
from test_grading_pipeline import CORRECT

fixture = GradingTeams('test_standard_informed_tie_break_can_choose_secondary')
fixture.setUp()
# The reviewer is bought only for a disagreement, and not for a pair that both give full marks, so the primary differs on a
# credit-bearing line, as the unit test of the same name does.
response, _ = fixture.grade(CORRECT, script=fixture.opinions(**{'alpha-vision': ('R2',)}))
aid, rid = fixture.plan['id'], response['id']
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{address}/#grading/{aid}?tab=review&browse=all&response={rid}')
        expect(page.get_by_text('Finalized automatically: the grading team reached agreement and all checks passed. You can inspect or change this grade.')).to_be_visible()
        checks = page.locator('[data-independent-checks]')
        checks.locator(':scope > summary').click()
        expect(checks.get_by_text('Cloud team: agreement reached', exact=True)).to_be_visible()
        expect(checks.get_by_text('The additional reviewer agrees with the second grader', exact=False)).to_be_visible()
        checks.get_by_text('Model assessments', exact=True).click()
        expect(checks.get_by_text('Additional reviewer · gamma-vision:', exact=False)).to_be_visible()
        assert not checks.get_by_text('Adversarial reviewer', exact=False).count()
        expect(page.locator('.grading-item-reason summary[aria-label*="grading history"]').first).to_be_visible()
        assert not page.locator('.grading-item-reason summary[aria-label*="graders disagree"]').count()
        # The standing explanation was adopted from the SECOND grader, even though
        # its endpoint is now the effective primary model. History retains each role.
        reason = page.locator('.grading-item-reason').first
        reason.locator('summary').click()
        expect(reason.locator(':scope > p').first.locator('.grading-model-label')).to_have_attribute('data-grading-role', 'second')
        expect(reason.locator(':scope > p').first.locator('.grading-model-comment')).to_have_attribute('data-grading-role', 'second')
        for role in ('primary', 'second', 'verifier'):
            label = checks.locator(f'.grading-model-label[data-grading-role="{role}"]').first
            comment = page.locator(f'.grading-item-reason .grading-model-comment[data-grading-role="{role}"]').first
            expect(comment).to_be_visible()
            assert label.evaluate('el => getComputedStyle(el).color') == comment.evaluate('el => getComputedStyle(el).color')
        # Cloud is the only cloud scheme and always carries both reviewer roles. The removed schemes have no controls left.
        page.get_by_role('button', name='Compute settings', exact=True).click()
        popup = page.get_by_role('dialog', name='Compute settings', exact=True)
        standard = popup.locator('[name="tier-cloud_standard-verifier"]')
        flags = popup.locator('[name="tier-cloud_standard-flag_reviewer"]')
        for field in (standard, flags):
            expect(field).to_be_hidden()
        for removed in ('one_shot_model', 'tag_team_model', 'jev_routing_model_1', 'tier-cloud_high_assurance-verifier', 'final_arbitrator', 'jev_tiebreak'):
            expect(popup.locator(f'[name="{removed}"]')).to_have_count(0)
        expect(popup.locator('[name="default_tier"]')).to_have_count(2)  # Cloud and Fully local.
        expect(popup.locator('label.compute-mode').first).to_contain_text('Cloud')
        popup.locator('input[name="default_tier"][value="cloud_standard"]').check()
        expect(standard).to_be_visible()
        expect(flags).to_be_visible()
        expect(popup.get_by_label('Handwriting reader', exact=True)).to_be_visible()
        expect(popup.get_by_text('Called only for a grading disagreement.', exact=False)).to_be_visible()
        popup.locator('[name="provider-tier-cloud_standard-flag_reviewer"]').select_option('custom')
        flags.select_option('alpha-vision')
        popup.locator('input[name="default_tier"][value="local"]').check()
        for field in (standard, flags):
            expect(field).to_be_hidden()
        popup.locator('input[name="default_tier"][value="cloud_standard"]').check()
        expect(flags).to_have_value('alpha-vision')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.get_by_role('button', name='Save compute settings', exact=True)).to_be_disabled()
        assert fixture.frq.settings()['settings']['tiers']['cloud_standard']['flag_reviewer'] == 'alpha-vision'
        popup.locator('input[name="default_tier"][value="local"]').check()
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        expect(standard).to_be_visible()
        # One appearance, desktop only (DESIGN-LANGUAGE): in the narrowest desktop window checked nothing scrolls sideways.
        page.set_viewport_size({'width': 1280, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.keyboard.press('Escape')
        assert not errors, errors
        browser.close()
        print('Grading teams UI passed: informed tie-break history, automatic final grade, settings and the desktop layout.')
finally:
    server.close()
    fixture.doCleanups()
