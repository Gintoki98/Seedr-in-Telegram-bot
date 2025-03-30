import json
import os
import time
from pathlib import Path
from seedrcc import Login
from cryptography.fernet import Fernet


class AuthManager:
    def __init__(self, storage_file='user_tokens.json', encryption_key=None):
        self.storage_file = Path(storage_file)
        self.encryption_key = encryption_key
        self.fernet = Fernet(encryption_key) if encryption_key else None
        self._ensure_storage_file()

    def _ensure_storage_file(self):
        if not self.storage_file.exists():
            with open(self.storage_file, 'w') as f:
                json.dump({}, f)

    def _encrypt(self, data):
        if self.fernet:
            return self.fernet.encrypt(data.encode()).decode()
        return data

    def _decrypt(self, data):
        if self.fernet and data:
            return self.fernet.decrypt(data.encode()).decode()
        return data

    def save_user_token(self, user_id, token):
        data = self._load_data()
        data[str(user_id)] = {
            'token': self._encrypt(token),
            'last_updated': int(time.time())
        }
        self._save_data(data)

    def get_user_token(self, user_id):
        data = self._load_data()
        user_data = data.get(str(user_id))
        if user_data and 'token' in user_data:
            return self._decrypt(user_data['token'])
        return None

    def delete_user_token(self, user_id):
        data = self._load_data()
        if str(user_id) in data:
            del data[str(user_id)]
            self._save_data(data)
            return True
        return False

    def _load_data(self):
        try:
            with open(self.storage_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_data(self, data):
        temp_file = f"{self.storage_file}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, self.storage_file)

    def generate_device_code(self):
        seedr = Login()
        device_code = seedr.getDeviceCode()
        return {
            'login_instance': seedr,
            'device_code': device_code['device_code'],
            'user_code': device_code['user_code'],
            'verification_url': 'https://seedr.cc/devices'
        }

    def poll_authorization(self, login_instance, device_code):
        while True:
            response = login_instance.authorize(device_code)

            if 'access_token' in response:
                return login_instance.token

            if response.get('error') == 'authorization_pending':
                time.sleep(5)
                continue

            raise Exception(f"Authorization failed: {response}")