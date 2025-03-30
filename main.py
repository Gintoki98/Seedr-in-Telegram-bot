import asyncio
import os
import json
import time
from pathlib import Path
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from seedrcc import Login, Seedr
from cryptography.fernet import Fernet

# Configuration
API_ID = int(os.getenv('TELEGRAM_API_ID'))  # Changed to standard naming
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')  # Generate with Fernet.generate_key()

class AuthManager:
    def __init__(self, storage_file='user_tokens.json'):
        self.storage_file = Path(storage_file)
        self.fernet = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None
        self._ensure_storage()

    def _ensure_storage(self):
        if not self.storage_file.exists():
            with open(self.storage_file, 'w') as f:
                json.dump({}, f)

    def _encrypt(self, data):
        return self.fernet.encrypt(data.encode()).decode() if self.fernet else data

    def _decrypt(self, data):
        return self.fernet.decrypt(data.encode()).decode() if self.fernet and data else data

    def save_token(self, user_id, token):
        data = self._load_data()
        data[str(user_id)] = {
            'token': self._encrypt(token),
            'last_updated': int(time.time())
        }
        self._save_data(data)

    def get_token(self, user_id):
        data = self._load_data()
        if str(user_id) in data:
            return self._decrypt(data[str(user_id)]['token'])
        return None

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

# Initialize clients
client = TelegramClient('seedr_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
auth_manager = AuthManager()

# Dictionary to track ongoing authentications
ongoing_auths = {}

async def get_user_account(user_id):
    """Get or initialize a Seedr account for a user"""
    token = auth_manager.get_token(user_id)
    if token:
        account = Seedr(token=token)
        try:
            if account.testToken().get('result'):
                return account
        except Exception:
            pass
    return None


# Helper function to create folder keyboard
def create_folder_keyboard(folders):
    buttons = []
    for folder in folders:
        # Safely get folder ID and name
        folder_id = str(folder.get('id', '0'))
        folder_name = folder.get('name', 'Unnamed Folder')
        buttons.append(
            [Button.inline(f"📁 {folder_name}", data=f"folder_{folder_id}")]
        )
    buttons.append([Button.inline("🔄 Refresh", data="refresh_folders")])
    buttons.append([Button.inline("🏠 Root Folder", data="folder_0")])
    return buttons


# Helper function to create file keyboard
def create_file_keyboard(files, folder_id):
    buttons = []
    for file in files:
        # More robust file ID extraction
        file_id = str(file.get('folder_file_id') or file.get('id') or file.get('file_id') or '0')
        file_name = file.get('name', 'Unnamed File')
        buttons.append(
            [Button.inline(f"📄 {file_name}", data=f"file_{file_id}_{folder_id}")]
        )
    buttons.append([Button.inline("⬅️ Back to Folders", data="list_folders")])
    buttons.append([Button.inline("🔄 Refresh", data=f"refresh_files_{folder_id}")])
    return buttons


# Command handlers
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handle /start command with user authentication flow"""
    user_id = event.sender_id
    user_token = auth_manager.get_token(user_id)

    # Check if user already has a valid token
    if user_token:
        account = Seedr(token=user_token)
        try:
            if account.testToken().get('result'):
                welcome_msg = """
                🌟 **Seedr Account Manager** 🌟

                ✅ Your Seedr account is connected!

                **Available Commands:**
                /folders - List your folders
                /storage - Check account usage
                /addmagnet - Add torrent via magnet link
                /help - Show help
                """
                buttons = [
                    [Button.inline("📂 List Folders", "list_folders")],
                    [Button.inline("💾 Check Storage", "check_storage")],
                    [Button.inline("🔗 Unlink Account", "unlink_account")]
                ]
                await event.respond(welcome_msg, buttons=buttons)
                return
        except Exception:
            pass  # Token is invalid, proceed to auth flow

    # User needs to authenticate
    welcome_msg = """
    🌟 **Seedr Account Manager** 🌟

    🔒 You need to connect your Seedr account first.

    This will:
    1. Generate a one-time code
    2. Open Seedr's authorization page
    3. Wait for you to confirm
    """
    buttons = [
        [Button.inline("🔗 Connect Seedr Account", "start_auth")],
        [Button.url("ℹ️ What is Seedr?", "https://seedr.cc")]
    ]
    await event.respond(welcome_msg, buttons=buttons)


@client.on(events.CallbackQuery(data=b'start_auth'))
async def start_auth_handler(event):
    """Begin Seedr authentication process"""
    user_id = event.sender_id

    try:
        # Generate device code
        seedr = Login()
        device_code = seedr.getDeviceCode()

        # Store the login instance for polling
        ongoing_auths[user_id] = {
            'login_instance': seedr,
            'device_code': device_code['device_code'],
            'expires_at': time.time() + 300  # 5 minute expiry
        }

        auth_msg = """
        🔑 **Authorization Steps**:

        1. Visit: [Seedr Device Auth](https://seedr.cc/devices)
        2. Enter this code: 
        ```
        {user_code}
        ```
        3. Click "Authorize"
        4. Come back here and click "I've Authorized"
        """.format(user_code=device_code['user_code'])

        buttons = [
            [Button.url("🔗 Open Authorization", url="https://seedr.cc/devices")],
            [Button.inline("✅ I've Authorized", data="check_auth")],
            [Button.inline("❌ Cancel", data="cancel_auth")]
        ]

        await event.edit(auth_msg, buttons=buttons)
    except Exception as e:
        await event.respond(f"❌ Failed to start authentication: {str(e)}")
        import traceback
        traceback.print_exc()


@client.on(events.CallbackQuery(data=b'check_auth'))
async def check_auth_handler(event):
    """Check if user has completed authorization"""
    user_id = event.sender_id

    if user_id not in ongoing_auths:
        await event.respond("❌ No active authorization session. Start with /start")
        return

    auth_data = ongoing_auths[user_id]

    try:
        # Check if authorization expired
        if time.time() > auth_data['expires_at']:
            del ongoing_auths[user_id]
            await event.respond("⌛ Authorization session expired. Please start again.")
            return

        await event.respond("⏳ Checking authorization...")
        response = auth_data['login_instance'].authorize(auth_data['device_code'])

        if response and 'access_token' in response:
            # Save the valid token
            auth_manager.save_token(user_id, auth_data['login_instance'].token)
            del ongoing_auths[user_id]

            await event.respond(
                "✅ Account connected successfully!\n\n"
                "You can now use all Seedr features.",
                buttons=[
                    [Button.inline("📂 List Folders", "list_folders")],
                    [Button.inline("🏠 Main Menu", "main_menu")]]
            )
        else:
            await event.respond("❌ Not authorized yet. Please complete the steps.")
    except Exception as e:
        await event.respond(f"❌ Authorization failed: {str(e)}")
        import traceback
        traceback.print_exc()


@client.on(events.CallbackQuery(data=b'cancel_auth'))
async def cancel_auth_handler(event):
    """Cancel ongoing authentication"""
    user_id = event.sender_id
    if user_id in ongoing_auths:
        del ongoing_auths[user_id]
    await event.respond("❌ Authorization cancelled.")


@client.on(events.CallbackQuery(data=b'unlink_account'))
async def unlink_account_handler(event):
    """Remove stored Seedr credentials"""
    user_id = event.sender_id
    auth_manager.save_token(user_id, None)  # Clear token
    await event.respond(
        "✅ Account unlinked successfully!\n\n"
        "You can reconnect anytime with /start",
        buttons=[
            [Button.inline("🔗 Reconnect Account", "start_auth")]
        ])


async def verify_user(event):
    """Check if user is authenticated and return their Seedr instance"""
    user_id = event.sender_id
    user_token = auth_manager.get_token(user_id)

    if not user_token:
        await event.respond(
            "🔒 You need to connect your Seedr account first!\n\n"
            "Use /start to begin authentication.",
            buttons=[Button.inline("🔗 Connect Account", "start_auth")]
        )
        return None

    try:
        account = Seedr(token=user_token)
        if account.testToken().get('result'):
            return account
        await event.respond(
            "❌ Your session has expired.\n"
            "Please reconnect your Seedr account.",
            buttons=[Button.inline("🔗 Reconnect", "start_auth")]
        )
    except Exception as e:
        await event.respond(f"❌ Account error: {str(e)}")

    return None


@client.on(events.NewMessage(pattern='/folders'))
async def list_folders_handler(event):
    """List all folders in user's Seedr account"""
    account = await verify_user(event)
    if not account:
        return

    try:
        response = account.listContents(contentType='folder')
        folders = response.get('folders', [])

        if not folders:
            await event.respond("📂 Your Seedr account has no folders yet.")
            return

        msg = "📂 **Your Folders:**\n\n"
        for folder in folders:
            msg += f"• {folder.get('name', 'Unnamed')} (ID: `{folder.get('id', '?')}`)\n"

        buttons = create_folder_keyboard(folders)
        await event.respond(msg, buttons=buttons)
    except Exception as e:
        await event.respond(f"❌ Error listing folders: {str(e)}")


@client.on(events.NewMessage(pattern='/storage'))
async def storage_handler(event):
    """Check user's account storage space"""
    account = await verify_user(event)
    if not account:
        return

    try:
        response = account.getMemoryBandwidth()
        max_space = int(response['space_max']) / (1024 ** 3)  # GB
        used_space = int(response['space_used']) / (1024 ** 3)  # GB
        percent_used = (used_space / max_space) * 100

        msg = (
            "💾 **Your Seedr Storage**\n\n"
            f"▰ Used: **{used_space:.2f}GB** ({percent_used:.1f}%)\n"
            f"▰ Free: **{(max_space - used_space):.2f}GB**\n"
            f"▰ Total: **{max_space:.2f}GB**\n\n"
            f"📊 Bandwidth Used: {int(response['bandwidth_used']) / (1024 ** 3):.2f}GB"
        )
        await event.respond(msg)
    except Exception as e:
        await event.respond(f"❌ Error checking storage: {str(e)}")


@client.on(events.NewMessage(pattern='/download'))
async def download_handler(event):
    """Download a file by ID"""
    account = await verify_user(event)
    if not account:
        return

    args = event.message.text.split()
    if len(args) < 2:
        await event.respond("Usage: `/download <fileId>`\nExample: `/download 12345`")
        return

    file_id = args[1]
    try:
        await event.respond("⏳ Generating download link...")
        response = account.fetchFile(fileId=file_id)

        if response.get('url'):
            file_name = response.get('name', 'file')
            buttons = [
                [Button.url("⬇️ Download Now", url=response['url'])],
                [Button.inline("🗑️ Delete File", data=f"delete_file_{file_id}")]
            ]
            await event.respond(
                f"🔗 **Download Ready**\n"
                f"📄 {file_name}\n"
                f"Link valid for 24 hours",
                buttons=buttons
            )
        else:
            await event.respond("❌ Couldn't generate download link. Check the file ID.")
    except Exception as e:
        await event.respond(f"❌ Download error: {str(e)}")


@client.on(events.NewMessage(pattern='/addmagnet'))
async def add_magnet_handler(event):
    """Add torrent via magnet link"""
    account = await verify_user(event)
    if not account:
        return

    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        await event.respond(
            "Usage: `/addmagnet <magnet_link>`\n\n"
            "Example: `/addmagnet magnet:?xt=urn:btih:...`"
        )
        return

    magnet_link = args[1]
    try:
        await event.respond("⏳ Adding torrent to your Seedr account...")
        response = account.addTorrent(magnetLink=magnet_link)

        if response.get('result'):
            await event.respond(
                "✅ Torrent added successfully!\n\n"
                "It may take several minutes to start downloading.\n"
                "Use /folders to check progress."
            )
        else:
            await event.respond("❌ Failed to add torrent. The link may be invalid.")
    except Exception as e:
        await event.respond(f"❌ Torrent error: {str(e)}")


@client.on(events.NewMessage(pattern='/delete'))
async def delete_handler(event):
    """Delete file/folder by ID"""
    account = await verify_user(event)
    if not account:
        return

    args = event.message.text.split()
    if len(args) < 3:
        await event.respond(
            "Usage: `/delete <type> <id>`\n\n"
            "Examples:\n"
            "• `/delete file 12345`\n"
            "• `/delete folder 67890`"
        )
        return

    item_type = args[1].lower()
    item_id = args[2]

    try:
        if item_type == 'file':
            response = account.deleteFile(fileId=item_id)
            success_msg = "🗑️ File deleted successfully!"
        elif item_type == 'folder':
            response = account.deleteFolder(folderId=item_id)
            success_msg = "🗑️ Folder deleted successfully!"
        else:
            await event.respond("❌ Invalid type. Use 'file' or 'folder'.")
            return

        if response.get('result'):
            await event.respond(success_msg)
        else:
            await event.respond(f"❌ Failed to delete {item_type}.")
    except Exception as e:
        await event.respond(f"❌ Deletion error: {str(e)}")


# Callback query handlers
@client.on(events.CallbackQuery(data=b'list_folders'))
async def list_folders_callback(event):
    """Handle folder list callback"""
    account = await verify_user(event)
    if not account:
        return

    try:
        response = account.listContents(contentType='folder')
        folders = response.get('folders', [])

        if not folders:
            await event.respond("📂 Your Seedr account has no folders yet.")
            return

        msg = "📂 **Your Folders**\n\n"
        msg += "\n".join([
            f"• {f.get('name', 'Unnamed')} (ID: `{f.get('id', '?')}`)"
            for f in folders
        ])

        await event.edit(
            msg,
            buttons=create_folder_keyboard(folders)
        )
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


@client.on(events.CallbackQuery(data=b'check_storage'))
async def check_storage_callback(event):
    """Handle storage check callback"""
    account = await verify_user(event)
    if not account:
        return

    try:
        response = account.getMemoryBandwidth()
        used = int(response['space_used']) / (1024 ** 3)
        total = int(response['space_max']) / (1024 ** 3)

        await event.edit(
            f"💾 **Your Storage**\n\n"
            f"▰ Used: **{used:.2f}GB** of {total:.2f}GB\n"
            f"▰ {used / total * 100:.1f}% full\n\n"
            f"📊 Bandwidth: {int(response['bandwidth_used']) / (1024 ** 3):.2f}GB",
            buttons=[Button.inline("🔄 Refresh", "check_storage")]
        )
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


@client.on(events.CallbackQuery(pattern=b'folder_(.*)'))
async def folder_contents_callback(event):
    """Show folder contents with download options"""
    account = await verify_user(event)
    if not account:
        return

    folder_id = event.pattern_match.group(1).decode()

    try:
        contents = account.listContents(folderId=folder_id)
        files = contents.get('files', [])
        folders = contents.get('folders', [])

        msg = f"📂 **{contents.get('name', 'Folder')}**\n\n"

        if folders:
            msg += "📁 **Subfolders**\n" + "\n".join([
                f"• {f.get('name', 'Unnamed')} (ID: `{f.get('id', '?')}`)"
                for f in folders
            ]) + "\n\n"

        if files:
            msg += "**Files:**\n"
            for file in files:
                file_name = file.get('name', 'Unnamed File')
                # Try multiple possible ID fields
                file_id = str(file.get('folder_file_id') or file.get('id') or file.get('file_id') or '0')
                file_size = int(file.get('size', 0)) / (1024 ** 2)
                msg += f"📄 {file_name} (ID: `{file_id}`) - {file_size:.2f} MB\n"

        buttons = []
        if files or folders:
            buttons.append([
                Button.inline("📦 Download All", f"download_folder_{folder_id}")
            ])

        buttons.extend([
            [Button.inline(f"⬇️ {f.get('name', 'File')}", f"file_{f.get('id')}_{folder_id}")]
            for f in files  # Limit to 8 files
        ])

        buttons.append([
            Button.inline("🔄 Refresh", f"folder_{folder_id}"),
            Button.inline("⬅️ Back", "list_folders")
        ])

        await event.edit(msg, buttons=buttons)
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


@client.on(events.CallbackQuery(pattern=b'file_(.*)_(.*)'))
async def file_action_callback(event):
    """Handle file download with robust ID handling"""
    account = await verify_user(event)
    if not account:
        return

    file_id, folder_id = event.pattern_match.group(1).decode(), event.pattern_match.group(2).decode()

    try:
        # First verify the file exists and get proper ID
        folder_contents = account.listContents(folderId=folder_id)
        files = folder_contents.get('files', [])

        # Find the file by ID (checking multiple possible ID fields)
        target_file = None
        for file in files:
            if (str(file.get('id'))) == file_id or \
                    str(file.get('file_id')) == file_id or \
                    str(file.get('folder_file_id')) == file_id:
                target_file = file
                break

        if not target_file:
            await event.respond("❌ File not found in this folder")
            return

        # Use the most reliable ID field we found
        proper_file_id = str(target_file.get('id') or
                             target_file.get('file_id') or
                             target_file.get('folder_file_id'))

        await event.respond("⏳ Generating download link...")
        response = account.fetchFile(fileId=proper_file_id)

        if response.get('url'):
            file_name = target_file.get('name', 'File')
            file_size = int(target_file.get('size', 0)) / (1024 ** 2)

            buttons = [
                [Button.url("⬇️ Download Now", response['url'])],
                [
                    Button.inline("🗑️ Delete", f"delete_file_{proper_file_id}"),
                    Button.inline("⬅️ Back", f"folder_{folder_id}")
                ]
            ]

            await event.respond(
                f"🔗 **Download Ready**\n"
                f"📄 {file_name}\n"
                f"Size: {file_size:.2f}MB\n"
                f"Link valid for 24 hours",
                buttons=buttons
            )
        else:
            debug_msg = (
                "❌ Failed to generate download link\n\n"
                f"File ID used: {proper_file_id}\n"
                f"API Response: {response}"
            )
            await event.respond(debug_msg)
    except Exception as e:
        error_msg = (
            f"❌ Download Error: {str(e)}\n\n"
            f"File ID attempted: {file_id}\n"
            f"Folder ID: {folder_id}"
        )
        await event.respond(error_msg)
        import traceback
        traceback.print_exc()


@client.on(events.CallbackQuery(pattern=b'download_folder_(.*)'))
async def download_folder_callback(event):
    """Handle folder archive download"""
    account = await verify_user(event)
    if not account:
        return

    folder_id = event.pattern_match.group(1).decode()

    try:
        msg = await event.respond("⏳ Creating ZIP archive... (This may take minutes for large folders)")
        response = account.createArchive(folderId=folder_id)

        if response.get('archive_url'):
            folder = account.listContents(folderId=folder_id)
            buttons = [
                [Button.url("📦 Download Archive", response['archive_url'])],
                [Button.inline("🔄 Refresh", f"download_folder_{folder_id}")]
            ]

            await msg.edit(
                f"✅ **{folder.get('name', 'Folder')} Archive Ready**\n"
                f"🔗 {response['archive_url']}\n\n"
                f"⚠️ Link expires in 24 hours",
                buttons=buttons
            )
        else:
            await msg.edit("❌ Failed to create archive")
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


@client.on(events.CallbackQuery(pattern=b'delete_file_(.*)'))
async def delete_file_callback(event):
    """Handle file deletion"""
    account = await verify_user(event)
    if not account:
        return

    file_id = event.pattern_match.group(1).decode()

    try:
        response = account.deleteFile(fileId=file_id)
        if response.get('result'):
            await event.edit("🗑️ File deleted successfully!")
        else:
            await event.respond("❌ Failed to delete file")
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


@client.on(events.CallbackQuery(data=b'refresh_folders'))
async def refresh_folders_callback(event):
    """Refresh folder list"""
    await list_folders_callback(event)


@client.on(events.CallbackQuery(pattern=b'refresh_files_(.*)'))
async def refresh_files_callback(event):
    """Refresh file list"""
    await folder_contents_callback(event)

#DEBUG HANDLER
@client.on(events.NewMessage(pattern='/debug'))
async def debug_handler(event):
    """Debug command to check API response structure"""
    account = await verify_user(event)
    if not account:
        return

    try:
        # Get root folder contents as sample
        response = account.listContents(contentType='folder')
        folders = response.get('folders', [])

        if not folders:
            await event.respond("📂 👾 Your Seedr account has no folders yet.")
            return

        # Format the response for display
        debug_msg = "🔍 **👾 API Response Structure:**\n\n"
        debug_msg += f"👾 Folders: {len(response.get('folders', []))} items\n"
        if 'folders' in response and len(response['folders']) > 0:
            sample_folder = response['folders'][0]
            debug_msg += "👾 Sample Folder Structure:\n"
            for key, value in sample_folder.items():
                debug_msg += f"- {key}: {type(value).__name__}\n"

        debug_msg += f"\n👾 Files: {len(response.get('files', []))} items\n"
        if 'files' in response and len(response['files']) > 0:
            sample_file = response['files'][0]
            debug_msg += "👾 Sample File Structure:\n"
            for key, value in sample_file.items():
                debug_msg += f"- {key}: {type(value).__name__}\n"

        await event.respond(debug_msg)
    except Exception as e:
        await event.respond(f"❌👾 Debug error: {str(e)}")

# Run the bot
print("Seedr Account Manager Bot is running...")
client.run_until_disconnected()