# syntax=docker/dockerfile:1.7

# Use Debian-based image so sharp works without compiling native deps
FROM node:20-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-bookworm-slim AS dev
WORKDIR /app
ENV NODE_ENV=development
# Bring node_modules from deps stage
COPY --from=deps /app/node_modules ./node_modules
# Source will be bind-mounted in compose
EXPOSE 3000
CMD ["npm", "run", "dev"]
