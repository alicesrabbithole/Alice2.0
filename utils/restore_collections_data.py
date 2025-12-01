import os
import shutil

# CONFIGURATION
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
DATA_FILE = "collected_pieces.json"
BACKUP_DIR = r"C:\Users\brian\Desktop\Alice2.0\backups"

def list_backups():
    return sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith("collected_pieces_") and f.endswith(".json")],
        reverse=True
    )

def restore_backup(backup_filename):
    src = os.path.join(BACKUP_DIR, backup_filename)
    dst = os.path.join(DATA_DIR, DATA_FILE)
    if not os.path.exists(src):
        print(f"Backup file {backup_filename} does not exist.")
        return
    shutil.copy2(src, dst)
    print(f"Restored {backup_filename} to {dst}")

if __name__ == "__main__":
    backups = list_backups()
    if not backups:
        print("No backups found in your backups folder.")
    else:
        print("Available backups (most recent first):")
        for i, fname in enumerate(backups):
            print(f"{i}: {fname}")
        choice = input("Choose a backup to restore by number: ")
        try:
            idx = int(choice)
            if 0 <= idx < len(backups):
                restore_backup(backups[idx])
            else:
                print("Invalid choice.")
        except ValueError:
            print("Invalid input.")