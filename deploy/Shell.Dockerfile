FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
COPY brand/idax ./public
RUN npm test && npm run build
FROM maven:3.9.9-eclipse-temurin-21 AS backend
WORKDIR /src
COPY pom.xml ./
COPY src src
RUN mvn -B -DskipTests package
FROM eclipse-temurin:21-jre-alpine
RUN addgroup -S idax && adduser -S -G idax -u 10001 idax
WORKDIR /app
COPY --from=backend /src/target/idax-shell-*.jar app.jar
COPY --from=frontend /src/frontend/dist /app/public
USER 10001
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app/app.jar","--spring.web.resources.static-locations=file:/app/public/"]
