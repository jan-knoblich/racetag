#!/bin/sh
set -e

JS_FILE="/usr/share/nginx/html/script.js"
API_FILE="/usr/share/nginx/html/api.js"

# Replace placeholder with actual API key at runtime (if provided)
if [ -n "$RACETAG_FRONTEND_API_KEY" ] && [ -f "$JS_FILE" ]; then
  ESCAPED_KEY=$(printf '%s' "$RACETAG_FRONTEND_API_KEY" | sed -e 's/[\\/&]/\\&/g')
  sed -i "s#__RACETAG_FRONTEND_API_KEY__#${ESCAPED_KEY}#g" "$JS_FILE"
  if [ -f "$API_FILE" ]; then
    sed -i "s#__RACETAG_FRONTEND_API_KEY__#${ESCAPED_KEY}#g" "$API_FILE"
  fi
fi

# Replace placeholder with actual Backend URL at runtime (if provided)
if [ -n "$RACETAG_FRONTEND_BACKEND_URL" ] && [ -f "$JS_FILE" ]; then
  ESCAPED_URL=$(printf '%s' "$RACETAG_FRONTEND_BACKEND_URL" | sed -e 's/[\\/&]/\\&/g')
  sed -i "s#__RACETAG_FRONTEND_BACKEND_URL__#${ESCAPED_URL}#g" "$JS_FILE"
fi

exec "$@"
