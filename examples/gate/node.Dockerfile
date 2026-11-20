# The gate runs the project's checks on a snapshot of each milestone, without network, in this image.
# Dependencies are installed here, from the lockfile, and linked into the snapshot (gate_links).
# Build: docker build -f node.Dockerfile -t my-node-app-gate:local /path/to/my-node-app
FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
