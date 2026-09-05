[app]
title = MEXC Mobile Scalper
package.name = mexcmobilescalper
package.domain = com.mexcscalper
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,json,txt
version = 1.0.1
requirements = python3,kivy==2.3.1,requests
orientation = portrait
fullscreen = 0

# Android permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# Build settings
android.api = 33
android.minapi = 21
android.ndk = 23b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
