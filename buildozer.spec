[app]

# Application name
title = MEXC Mobile Scalper

# Package information
package.name = mexcmobilescalper
package.domain = com.mexcscalper

# Source
source.dir = .

# Files included in the APK
source.include_exts = py,png,jpg,jpeg,kv,json,txt

# Application version
version = 1.0.1

# Python / Kivy requirements
requirements = python3,kivy==2.3.1,requests

# Screen
orientation = portrait
fullscreen = 0

# Android permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# Android target
android.api = 35

# Minimum Android version
android.minapi = 21

# Android NDK
android.ndk = 28c

# NDK API
android.ndk_api = 21

# CPU architecture
android.archs = arm64-v8a

# AndroidX
android.enable_androidx = True

# Android entry point
android.entrypoint = org.kivy.android.PythonActivity

# Android theme
android.apptheme = "@android:style/Theme.NoTitleBar"

# Accept Android SDK licenses
android.accept_sdk_license = True

# Do not use private storage
android.private_storage = True

# Debug APK
android.debug_artifact = apk

# Python-for-Android
p4a.fork = kivy
p4a.branch = master

# Build settings
[buildozer]

log_level = 2
warn_on_root = 1