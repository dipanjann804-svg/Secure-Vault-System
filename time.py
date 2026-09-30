import time
import getpass

MAX_ATTEMPTS = 6
INITIAL_LOCKOUT = 30
LOCKOUT_MULTIPLIER = 2

def main():
    correct_password = getpass.getpass("Set your password: ")

    lockout_duration = INITIAL_LOCKOUT
    round_number = 1

    while True:
        print(f"\n--- Round {round_number} ---")
        attempts = 0

        while attempts < MAX_ATTEMPTS:
            password = getpass.getpass("Enter password: ")

            if password == correct_password:
                print("Access granted!")
                return

            attempts += 1
            remaining = MAX_ATTEMPTS - attempts
            print(f"Wrong password. Attempts remaining: {remaining}")

        print(f"Too many failed attempts. Locked for {lockout_duration} seconds.")
        lock_with_countdown(lockout_duration)

        lockout_duration *= LOCKOUT_MULTIPLIER
        round_number += 1


def lock_with_countdown(seconds):
    for remaining in range(seconds, 0, -1):
        print(f"Time remaining: {remaining}s", end="\r")
        time.sleep(1)
    print("You can try again now")

if __name__ == "__main__":
    main()