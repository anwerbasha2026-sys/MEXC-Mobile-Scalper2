# Android APK

This project is configured for Buildozer and GitHub Actions.

## Build on GitHub

1. Create a GitHub repository.
2. Upload the project files.
3. Push to `main` (or run the workflow manually from Actions).
4. Open GitHub -> Actions -> Build Android APK.
5. When the job finishes, download the `MEXC-Mobile-Scalper-APK` artifact.
6. Extract the artifact and install the `.apk` on Android.

The scanner remains configured to inspect the top 200 USDT pairs.

## Important

Do not put your MEXC API Key or Secret Key into GitHub source files.
Enter them inside the app only.

This build is a debug APK for testing. A signed release APK requires an Android signing key and release configuration.
