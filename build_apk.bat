@echo off
echo === Down4U APK Fresh Clean Build Script ===
set "JAVA_HOME=C:\Users\user\.jdks\temurin-21.0.12.1"
set "ANDROID_HOME=C:\Users\user\AppData\Local\Android\Sdk"
set "ANDROID_SDK_ROOT=C:\Users\user\AppData\Local\Android\Sdk"
set "PATH=%JAVA_HOME%\bin;%PATH%"

del /F /Q AnyVideoDownloader-debug.apk 2>nul
del /F /Q android\app\build\outputs\apk\debug\app-debug.apk 2>nul

echo Cleaning and Building APK...
cd android
call gradlew.bat clean assembleDebug
cd ..

if exist android\app\build\outputs\apk\debug\app-debug.apk (
    copy /Y android\app\build\outputs\apk\debug\app-debug.apk AnyVideoDownloader-debug.apk
    echo.
    echo === BUILD COMPLETE SUCCESS! ===
    dir AnyVideoDownloader-debug.apk
) else (
    echo.
    echo === BUILD FAILED! ===
)
