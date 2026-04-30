
import argparse
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List Gemini Files API files and optionally delete them."
    )
    delete_group = parser.add_mutually_exclusive_group()
    delete_group.add_argument(
        "--delete-failed",
        action="store_true",
        help="Delete files whose Gemini state is FAILED after listing them.",
    )
    delete_group.add_argument(
        "--delete-all",
        action="store_true",
        help="Delete all Gemini files after listing them.",
    )
    args = parser.parse_args()

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    files = list(client.files.list())

    print(f"{len(files)} file(s) in Gemini Files API:")
    failed_files = []
    for file_obj in files:
        state_name = file_obj.state.name
        print(f"  {file_obj.name}  state={state_name}  created={file_obj.create_time}")
        if state_name == "FAILED":
            failed_files.append(file_obj)

    if not args.delete_failed and not args.delete_all:
        return

    files_to_delete = files if args.delete_all else failed_files

    if not files_to_delete:
        if args.delete_all:
            print("No files to delete.")
        else:
            print("No failed files to delete.")
        return

    deleted = 0
    for file_obj in files_to_delete:
        client.files.delete(name=file_obj.name)
        deleted += 1
        if args.delete_all:
            print(f"Deleted file: {file_obj.name}")
        else:
            print(f"Deleted failed file: {file_obj.name}")

    if args.delete_all:
        print(f"Deleted {deleted} file(s).")
    else:
        print(f"Deleted {deleted} failed file(s).")


if __name__ == "__main__":
    main()
