# Simple Nginx-based static site for the frontend
FROM nginx:1.29.2-alpine

# Copy static files
COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY api.js /usr/share/nginx/html/api.js
COPY script.js /usr/share/nginx/html/script.js
COPY nginx.conf /etc/nginx/nginx.conf

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Minimal nginx config (optional: allow CORS for static if needed)
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
