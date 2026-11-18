# syntax=docker/dockerfile:1

FROM gradle:8.12-jdk21 AS build
WORKDIR /app

COPY settings.gradle build.gradle gradle.properties ./
RUN gradle --no-daemon dependencies --quiet || true

COPY src ./src
RUN gradle --no-daemon bootJar --quiet

FROM build AS test
CMD ["gradle", "--no-daemon", "test"]

FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

RUN useradd --create-home --uid 10001 appuser
COPY --from=build --chown=appuser:appuser /app/build/libs/*.jar app.jar
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=45s --retries=5 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8080 && printf 'GET /health HTTP/1.0\\r\\n\\r\\n' >&3 && head -1 <&3 | grep -q '200'"]

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
