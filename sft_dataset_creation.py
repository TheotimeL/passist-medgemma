import argparse
import asyncio
import json
import os
from pathlib import Path

# Load your existing policy tree to provide context to the teacher
TREE_PATH = "rheumatoid_arthritis_initial_auth_decision_tree.json"
CHECKPOINT_FILE = "sft_checkpoint.json"
OUTPUT_FILE = "medgemma_sft_dataset.jsonl"
MAX_RETRIES = 3


def load_checkpoint() -> set[str]:
    """Returns the set of already-completed file stems."""
    if Path(CHECKPOINT_FILE).exists():
        data = json.loads(Path(CHECKPOINT_FILE).read_text())
        return set(data.get("completed", []))
    return set()


def save_checkpoint(completed: set[str]):
    Path(CHECKPOINT_FILE).write_text(json.dumps({"completed": sorted(completed)}, indent=2))


def print_progress(done: int, total: int, errors: int):
    pct = done / total * 100 if total else 0
    print(f"  [{done}/{total}] {pct:.1f}% done  |  {total - done} remaining  |  {errors} errors",
          flush=True)


def build_labeling_prompt(note_text: str, policy_json: str) -> str:
    """Constructs the prompt that instructs the Teacher to label the note."""
    return (
        "You are an expert Medical Auditor. Your task is to extract evidence for Prior Authorization.\n\n"
        "## POLICY TREE\n"
        f"{policy_json}\n\n"
        "## PATIENT CLINICAL NOTE\n"
        f"{note_text}\n\n"
        "## INSTRUCTIONS\n"
        "For every LEAF criterion in the policy tree, determine if it is met based on the note.\n"
        "1. met: true (confirmed), false (explicitly contradicted), or null (absent/unclear).\n"
        "2. evidence: A verbatim quote (max 20 words) from the note. If absent, use 'Not mentioned'.\n"
        "3. Respond ONLY with a raw JSON array of objects containing: "
        '{"criterion_id": "...", "met": true/false/null, "evidence": "..."}\n\n'
        "Do not include markdown fences or explanations. Output valid JSON only."
    )


async def call_teacher_subagent(prompt: str, model: str) -> str:
    """Invokes the teacher model (Claude) to perform the labeling."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--model", model,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        out = stdout.decode().strip()
        err = stderr.decode().strip()
        raise RuntimeError(
            f"exit={proc.returncode} | stderr={err!r} | stdout={out!r}"
        )
    return stdout.decode().strip()


async def label_file(semaphore, txt_path, policy_json, model):
    """Processes a single .txt file with retries and JSON validation."""
    async with semaphore:
        note_text = txt_path.read_text(encoding="utf-8")
        prompt = build_labeling_prompt(note_text, policy_json)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw_json = await call_teacher_subagent(prompt, model)
                
                # Strip markdown fences if the model hallucinated them
                clean_json = raw_json.strip().removeprefix("```json").removesuffix("```").strip()
                clean_json = clean_json.removeprefix("```").strip()
                
                # Validate that the response is actually parseable JSON
                json.loads(clean_json) 

                full_instruction_prompt = f"Evaluate the clinical note for PA criteria:\n\n{note_text}"
                return {
                    "text": f"<start_of_turn>user\n{full_instruction_prompt}<end_of_turn>\n"
                            f"<start_of_turn>model\n{clean_json}<end_of_turn>"
                }
            except Exception as e:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts. Last Error: {e}")
                # Wait before retrying to allow rate limits to reset
                await asyncio.sleep(5)


async def process_and_return(f, sem, policy, model):
    """Wrapper to keep track of which file produced which result, fixing as_completed."""
    record = await label_file(sem, f, policy, model)
    return f, record


async def main(args):
    notes_dir = Path(args.dir)
    all_files = sorted(notes_dir.rglob("*.txt"))
    total = len(all_files)
    
    if total == 0:
        print(f"No .txt files found in {notes_dir}")
        return

    policy_json = Path(TREE_PATH).read_text()

    # --- Resume: skip already-completed files ---
    completed = load_checkpoint()
    pending = [f for f in all_files if f.stem not in completed]

    if completed:
        print(f"Resuming: {len(completed)}/{total} already done, {len(pending)} remaining.")
    else:
        print(f"Starting fresh: {total} files to label.")

    if not pending:
        print("Nothing to do. Delete sft_checkpoint.json to start over.")
        return

    # Open output file in append mode so completed records survive a restart
    out_f = open(OUTPUT_FILE, "a", encoding="utf-8")
    semaphore = asyncio.Semaphore(args.concurrency)

    # Create tasks using the wrapper function
    tasks = [
        asyncio.create_task(process_and_return(f, semaphore, policy_json, args.model))
        for f in pending
    ]

    done_count = len(completed)
    error_count = 0

    print_progress(done_count, total, error_count)

    for coro in asyncio.as_completed(tasks):
        try:
            # Unpack the file path and the record directly from the yielded coroutine
            src_file, record = await coro
            
            # Write to disk immediately and flush the buffer
            out_f.write(json.dumps(record) + "\n")
            out_f.flush()
            
            # Update checkpoint
            completed.add(src_file.stem)
            save_checkpoint(completed)
            done_count += 1
            
        except Exception as e:
            error_count += 1
            print(f"\n  ERROR: {e}")

        print_progress(done_count, total, error_count)

    out_f.close()

    print(f"\nDone. {done_count}/{total} records written to {OUTPUT_FILE}.")
    if error_count:
        print(f"  {error_count} files failed — re-run the script to retry them.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Directory containing .txt clinical notes")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929",
                        help="Claude model to use for labeling")
    asyncio.run(main(parser.parse_args()))