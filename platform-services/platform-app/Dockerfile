FROM eclipse-temurin:21-jdk-alpine AS builder

WORKDIR /workspace
COPY gradlew gradlew.bat settings.gradle.kts build.gradle.kts ./
COPY gradle/ gradle/
COPY platform-services/platform-core/ platform-services/platform-core/
COPY platform-services/platform-app/ platform-services/platform-app/
RUN chmod +x gradlew \
    && ./gradlew :platform-services:platform-app:bootJar --no-daemon \
    && cp platform-services/platform-app/build/libs/*.jar /workspace/platform-app.jar

FROM eclipse-temurin:21-jre-alpine

RUN addgroup -S xingjing && adduser -S -G xingjing xingjing
WORKDIR /app
COPY --from=builder /workspace/platform-app.jar /app/platform-app.jar
USER xingjing
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/platform-app.jar"]
