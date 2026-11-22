# Build and test environment for the Android app: JDK 17, the Android SDK
# (platform 35, build tools), Gradle, and an emulator with an x86_64 test image
# for end-to-end runs on a host with /dev/kvm (aibox1).
#   docker build -t indie-recorder-android android
#   docker run --rm -v $PWD/android:/src -w /src indie-recorder-android ./gradlew assembleDebug
FROM eclipse-temurin:17-jdk-jammy
ENV ANDROID_HOME=/opt/android-sdk \
    GRADLE_USER_HOME=/cache/gradle
RUN apt-get update && apt-get install -y --no-install-recommends \
        unzip curl ca-certificates python3 ffmpeg \
        libpulse0 libnss3 libxcomposite1 libxcursor1 libxi6 libxtst6 libasound2 libgl1 libdbus-1-3 \
        libxkbfile1 libxdamage1 libxrandr2 libxkbcommon0 libx11-xcb1 libxcb-cursor0 \
    && rm -rf /var/lib/apt/lists/*
ARG CMDLINE_TOOLS=commandlinetools-linux-11076708_latest.zip
RUN mkdir -p $ANDROID_HOME/cmdline-tools && cd /tmp \
    && curl -fsSLo tools.zip https://dl.google.com/android/repository/$CMDLINE_TOOLS \
    && unzip -q tools.zip -d $ANDROID_HOME/cmdline-tools \
    && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest && rm tools.zip
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator
RUN yes | sdkmanager --licenses > /dev/null \
    && sdkmanager --install "platform-tools" "platforms;android-35" "build-tools;35.0.0" > /dev/null
RUN sdkmanager --install "emulator" "system-images;android-34;aosp_atd;x86_64" > /dev/null
# The test image above draws nothing (no screenshots); this one does.
RUN sdkmanager --install "system-images;android-34;default;x86_64" > /dev/null
ARG GRADLE=8.11.1
RUN curl -fsSLo /tmp/gradle.zip https://services.gradle.org/distributions/gradle-$GRADLE-bin.zip \
    && unzip -q /tmp/gradle.zip -d /opt && ln -s /opt/gradle-$GRADLE/bin/gradle /usr/local/bin/gradle && rm /tmp/gradle.zip
