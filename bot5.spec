# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['bot5.py'],
    pathex=[],
    binaries=[
        ('libopus-0.dll', '.'),  # Include Opus DLL
    ],
    datas=[
        ('led.ico', '.'),  # Include icon file
        ('config.json', '.'),  # Add this line
    ],
    hiddenimports=[
        'discord',
        'discord.ext.commands',
        'discord.ext.voice_recv',
        'blinkstick',
        'keyboard',
        'gtts',
        'pystray',
        'PIL.Image',
        'nacl',
        'cffi',
        '_cffi_backend',
        'pynacl',
        'nacl.bindings._sodium',
        'nacl.bindings.crypto_aead',
        'nacl.bindings.crypto_box',
        'nacl.bindings.crypto_core',
        'nacl.bindings.crypto_generichash',
        'nacl.bindings.crypto_hash',
        'nacl.bindings.crypto_pwhash',
        'nacl.bindings.crypto_scalarmult',
        'nacl.bindings.crypto_secretbox',
        'nacl.bindings.crypto_shorthash',
        'nacl.bindings.crypto_sign',
        'nacl.bindings.randombytes',
        'nacl.bindings.sodium_core',
        'nacl.bindings.utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DiscordLEDBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep True for debugging
    icon='led.ico'
)