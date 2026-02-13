# Multi-stage build for Zoom App

# Stage 1: Build React app
FROM node:18-alpine as builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy source
COPY public/ ./public/
COPY src/ ./src/

# Build React app
RUN npm run build

# Stage 2: Production server
FROM node:18-alpine

WORKDIR /app

# Copy server package
COPY server/package*.json ./

# Install server dependencies
RUN npm ci --only=production

# Copy server code
COPY server/index.js ./

# Copy built React app
COPY --from=builder /app/build ./public

# Install serve to host static files
RUN npm install -g serve

# Expose port
ENV PORT=8080
EXPOSE 8080

# Start command - serves both static files and API
CMD ["node", "index.js"]
