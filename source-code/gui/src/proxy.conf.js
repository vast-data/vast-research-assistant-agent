const PROXY_CONFIG = {
  "/users": {
    "target": "http://127.0.0.1:8000",
    "secure": false,
    "changeOrigin": true,
    "logLevel": "debug", // info debug warn error silent
    "headers": {'Access-Control-Allow-Origin': '*'}
  },
  "/token": {
    "target": "http://127.0.0.1:8000",
    "secure": false,
    "changeOrigin": true,
    "logLevel": "debug", // info debug warn error silent
    "headers": {'Access-Control-Allow-Origin': '*'}
  },
  "/api/v1": {
    "target": "http://127.0.0.1:8000",
    "secure": false,
    "changeOrigin": true,
    "logLevel": "debug", // info debug warn error silent
    "headers": {'Access-Control-Allow-Origin': '*'}
  },
};

console.log(`Proxy to: http://127.0.0.1:8000 \n`)
module.exports = PROXY_CONFIG;
