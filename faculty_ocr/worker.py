"""Offline model child. Inputs are normalized images, never paths from a document."""
import json
from pathlib import Path
import resource
import time
from importlib.metadata import version

from .config import MODEL_ID, MODEL_REVISION, TASKS


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (480, 480))  # one run reads a whole first-page batch (callers send 48 pages; client.MAX_COVERS allows 64)
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024**2, 2 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    import torch
    torch.set_num_threads(2)
    request = json.loads(Path("/work/request.json").read_text())
    available = torch.cuda.is_available()
    versions = {name: version(name) for name in ("torch", "torchvision", "transformers", "huggingface-hub", "safetensors")}
    if request.get("operation") == "status":
        result = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "cuda_available": available,
                  "gpu": torch.cuda.get_device_name(0) if available else None, "versions": versions, "network": False}
    else:
        from PIL import Image
        from transformers import AutoProcessor, GlmOcrForConditionalGeneration
        if request["device"] == "cuda" and not available:
            raise RuntimeError("CUDA was requested but is unavailable.")
        device = "cuda" if available and request["device"] != "cpu" else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        if device == "cuda":
            torch.cuda.set_per_process_memory_fraction(.75)
        else:
            resource.setrlimit(resource.RLIMIT_AS, (16 * 1024**3, 16 * 1024**3))
        started = time.monotonic()
        processor = AutoProcessor.from_pretrained("/model", local_files_only=True, trust_remote_code=False)
        model = GlmOcrForConditionalGeneration.from_pretrained("/model", local_files_only=True,
            trust_remote_code=False, use_safetensors=True, dtype=dtype, attn_implementation="sdpa").to(device).eval()
        def read_image(name):
            with Image.open("/work/" + name) as source:
                image = source.convert("RGB")
            messages = [{"role": "user", "content": [{"type": "image", "image": image},
                         {"type": "text", "text": TASKS[request["task"]]}]}]
            inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                   return_dict=True, return_tensors="pt").to(device)
            for name, value in inputs.items():
                if torch.is_floating_point(value):
                    inputs[name] = value.to(dtype)
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=request["max_new_tokens"], do_sample=False)
            completion = generated[0, inputs["input_ids"].shape[-1]:]
            return {"text": processor.decode(completion, skip_special_tokens=True).strip(),
                    "generated_tokens": len(completion), "truncated": len(completion) >= request["max_new_tokens"]}

        result = {"task": request["task"], "model_id": MODEL_ID, "revision": MODEL_REVISION, "device": device,
                  "dtype": str(dtype).removeprefix("torch."), "needs_review": True, "versions": versions, "network": False}
        if request.get("operation") == "covers":
            # Several first pages in this one model load, each crop kind read as a batch (one generation for up to `batch_size`
            # crops, left-padded). Every page's band upright; the band turned 180 degrees only where the upright one lacks one of the
            # caller's words (an upside-down sheet); the title box only on a first page whose band does not already match
            # `title_unless` (the version is often in the band), at that page's rotation.
            import re
            words = [re.compile(r"\b" + re.escape(word) + r"\b", re.I) for word in request["cover_words"]]
            unless = re.compile(request["title_unless"], re.I) if request.get("title_unless") else None
            size = request.get("batch_size", 8)
            stops = {processor.tokenizer.pad_token_id, *(model.generation_config.eos_token_id if isinstance(model.generation_config.eos_token_id, list)
                                                         else [model.generation_config.eos_token_id])}
            processor.tokenizer.padding_side = "left"

            def read_batch(regions, limit):
                reads = []
                for start in range(0, len(regions), size):
                    chunk = regions[start:start + size]
                    images = []
                    for region in chunk:
                        with Image.open("/work/" + region["name"]) as source:
                            images.append(source.convert("RGB"))
                    conversations = [[{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": TASKS[request["task"]]}]}]
                                     for image in images]
                    inputs = processor.apply_chat_template(conversations, tokenize=True, add_generation_prompt=True, return_dict=True,
                                                           return_tensors="pt", processor_kwargs={"padding": True}).to(device)
                    for name, value in inputs.items():
                        if torch.is_floating_point(value):
                            inputs[name] = value.to(dtype)
                    with torch.inference_mode():
                        generated = model.generate(**inputs, max_new_tokens=limit, do_sample=False)
                    for region, row in zip(chunk, generated[:, inputs["input_ids"].shape[-1]:].tolist()):
                        count = next((i for i, token in enumerate(row) if token in stops), len(row))
                        reads.append({**region, "text": processor.decode(row[:count], skip_special_tokens=True).strip(),
                                      "generated_tokens": count, "truncated": count >= limit})
                return reads

            # The model is loaded; the client prepares the crops meanwhile and says so with pages.json.
            deadline = time.monotonic() + 300
            while not Path("/work/pages.json").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("The pages to read never arrived.")
                time.sleep(0.05)
            pages = json.loads(Path("/work/pages.json").read_text())
            find = lambda page, kind, rotation: next(r for r in page["regions"] if r["kind"] == kind and r["rotation"] == rotation)
            cover = lambda text: all(word.search(text) for word in words)
            reads = [[band] for band in read_batch([find(page, "band", 0) for page in pages], request["max_new_tokens"])]
            chosen = {i: 0 for i, band in enumerate(reads) if cover(band[0]["text"])}
            turned = [i for i in range(len(pages)) if i not in chosen]
            for i, band in zip(turned, read_batch([find(pages[i], "band", 180) for i in turned], request["max_new_tokens"])):
                reads[i].append(band)
                if cover(band["text"]):
                    chosen[i] = 180
            titled = [i for i, rotation in sorted(chosen.items())
                      if not (unless and unless.search(next(r for r in reads[i] if r["rotation"] == rotation)["text"]))]
            for i, title in zip(titled, read_batch([find(pages[i], "title", chosen[i]) for i in titled], request["title_tokens"])):
                reads[i].append(title)
            result["pages"] = [{"reads": page_reads, "original_pixels": page["original_pixels"]} for page_reads, page in zip(reads, pages)]
        elif request.get("operation") == "pages":
            # Whole pages in this one model load, each read exactly as a single page is (read_image, one at a time). The model is
            # loaded; the client prepares the pages meanwhile and says how many with pages.json.
            deadline = time.monotonic() + 300
            while not Path("/work/pages.json").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("The pages to read never arrived.")
                time.sleep(0.05)
            count = json.loads(Path("/work/pages.json").read_text())
            if type(count) is not int or not 1 <= count <= request["inputs"] <= 8:
                raise RuntimeError("Unexpected page count.")
            result["load_seconds"] = round(time.monotonic() - started, 3)
            reads = []
            for index in range(count):
                began = time.monotonic()
                reads.append({**read_image(f"page-{index}.png"), "seconds": round(time.monotonic() - began, 3)})
            result["pages"] = reads
        elif request.get("regions"):
            result["reads"] = [{**region, **read_image(region["name"])} for region in request["regions"]]
        else:
            result.update(read_image("page.png"))
        result["batch_seconds" if "pages" in result else "seconds"] = round(time.monotonic() - started, 3)  # A page keeps its own.
        if device == "cuda":
            result["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
    Path("/work/result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Keep document text and third-party parser messages out of errors/logs.
        Path("/work/result.json").write_text(json.dumps({"error": "Local OCR failed", "error_type": type(error).__name__}))
