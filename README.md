# js-url-auto-reverse
#使用方式：
使用 js-url-auto-reverse 逆向站点 http://8.130.74.51:81/，得到登录接口账号密码加密密文的密钥、加密方法位置和关键调用链信息。

fetch("http://8.130.74.51:81/encrypt/rsa.php", {
  "headers": {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "content-type": "application/x-www-form-urlencoded",
    "proxy-connection": "keep-alive"
  },
  "referrer": "http://8.130.74.51:81/",
  "body": "data=EeOnl%2BS4gvNBCkvOwLTk8%2B8lkO74xrxSGvG9OiYho5XcIz2C8GmIaMbweXfGQfDsjd1VTpNEJXNBYGBMiV6N6gm5iQVPL5HUofBHjyjxLdcHWdJqh4H2xJMUsbXBeMGVlfumG7Ld9mLile26x7VF7gSqRybVNqvnSLXmGc6oo9g%3D",
  "method": "POST",
  "mode": "cors",
  "credentials": "include"
});
