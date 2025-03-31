from cryptography.fernet import Fernet

# Generate a new encryption key
encryption_key = Fernet.generate_key()

# Print it in a format you can copy-paste into your environment variables
print("Your encryption key (copy this exactly):")
print(encryption_key.decode())