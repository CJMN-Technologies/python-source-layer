import os
import subprocess
from dotenv import load_dotenv

ENV_PATH = ".env"

def push_secrets_to_github():
    """Reads the .env file and pushes the FB_ secrets to GitHub via GitHub CLI."""
    print("🚀 Pushing secrets to GitHub Actions...")
    
    # Check if GitHub CLI is installed and authenticated
    try:
        subprocess.run(["C:/Program Files/GitHub CLI/gh.exe", "auth", "status"], check=True, capture_output=True)
    except FileNotFoundError:
        print("❌ GitHub CLI ('gh') is not installed. Download it from: https://cli.github.com/")
        return
    except subprocess.CalledProcessError:
        print("❌ You are not logged into GitHub CLI. Run: \"C:\\Program Files\\GitHub CLI\\gh.exe\" auth login in your terminal first.")
        return

    load_dotenv(ENV_PATH)
    
    # Push all FB secrets
    for suffix in ["", "_2", "_3"]:
        for key in ["FB_C_USER", "FB_XS", "FB_DATR", "FB_FR", "FB_SB"]:
            env_var = f"{key}{suffix}"
            value = os.getenv(env_var)
            if value:
                print(f"   Pushing {env_var}...")
                subprocess.run(
                    ["C:/Program Files/GitHub CLI/gh.exe", "secret", "set", env_var],
                    input=value.encode(),
                    check=True
                )
    print("✅ All GitHub secrets updated successfully!")

if __name__ == "__main__":
    print("=== GitHub Secrets Updater ===")
    push_secrets_to_github()
