FROM apify/actor-node:20
COPY package*.json ./
RUN npm install --omit=dev --omit=optional --no-audit --no-fund
COPY . ./
CMD ["npm", "start", "--silent"]
