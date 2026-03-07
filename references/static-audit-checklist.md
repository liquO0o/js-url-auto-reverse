# Static Audit Checklist

1. 是否已定位请求发送点（fetch/xhr/axios）？
2. 是否已定位加密或编码点（CryptoJS/subtle/base64/hash）？
3. 是否已构建 Source -> Transform -> Sink 链路？
4. 是否已定位 key/iv/salt/nonce/timestamp 来源？
5. 是否区分“已证实”与“推断”？
6. 是否记录文件路径与行号证据？
