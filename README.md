# autovpn-switcher
VPN切换器，防止忘记切换

目前，越来越多的企业对“VPN”的监管慢慢变得严格了。有时候我们在家使用VPN工具后忘记切换。把电脑带到公司后，连上公司WIFI，你的VPN上网就会被公司记录了。可能会引起不必要的麻烦。

# 软件作用

这个软件的作用就是，当你打开这个软件后（可设置为“开机自启”）。这个软件就会实时监控你连接的WIFI名称。当你连接到了设置的公司WIFI，就会自动切换为**直连模式**。当你回到家中，软件发现你连的WIFI不是公司WIFI，就会自动切换为**代理模式**。这样免去了你自己去切换模式，或者说有时候忘记切换，软件会帮你自动去切换。

# 软件适配

由于这是我做出来玩的一个软件，所以得去慢慢的适配。适配情况如下：
| 软件 | 适配情况 |
|-------|--------|
| Clash Verge | ✅ |
| v2rayN | ❌ |
| Qv2ray | ❌ |
| Shadowrocket | ❌ |
| ClashX | ❌ |

# 使用指南
Clash Verge:
https://github.com/YFFanJK/autovpn-switcher/blob/main/clash-verge-use.md

# github文件说明
你可以在soft文件夹里面找到源代码

或者直接在下载.exe文件
