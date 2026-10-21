from backend.services.gmail_auth import get_gmail_credentials


def main():
    credentials = get_gmail_credentials()

    if credentials and credentials.valid:
        print("SUCCESS: Gmail authorization is valid.")
        print(f"Token file: {credentials.valid}")
    else:
        print("ERROR: Gmail authorization is not valid.")


if __name__ == "__main__":
    main()