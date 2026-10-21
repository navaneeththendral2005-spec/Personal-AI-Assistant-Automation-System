from backend.services.gmail_service import get_profile, list_emails


def main():
    print("\n--- FRIDAY Gmail Profile ---")

    profile = get_profile()

    print(f"Account: {profile['email_address']}")
    print(f"Total messages: {profile['messages_total']}")
    print(f"Total threads: {profile['threads_total']}")

    print("\n--- Recent Emails ---")

    emails = list_emails(max_results=5)

    if not emails:
        print("No emails returned.")
        return

    for index, email in enumerate(emails, start=1):
        print(f"\nEmail {index}")
        print(f"From: {email['sender']}")
        print(f"Subject: {email['subject']}")
        print(f"Date: {email['date']}")
        print(f"Snippet: {email['snippet'][:150]}")


if __name__ == "__main__":
    main()