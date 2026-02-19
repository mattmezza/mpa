FROM node:21-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.js ./

ENV PORT=3001
EXPOSE 3001

CMD ["npm", "start"]
