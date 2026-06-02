"""
@文件: __main__.py
@作者: 雷小鸥
@日期: 2026/5/27 18:30
@许可: MIT License
@描述: 使用演示
@版本: Version 0.2
"""
from .Diag import Service, DoIPConfig, KeepAliveConfig, RetryConfig


# ================== 使用演示 ==================
if __name__ == '__main__':
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # ---- 调用者实现 key_calculator ----
    def my_key_calculator(level: int, seed: bytes) -> bytes:
        """示例：外部自行管理 PIN Code 和 Key 算法。"""
        # 实际项目中从 secrets.yaml / HSM / 环境变量获取 PIN
        raise NotImplementedError("请实现 key_calculator：PIN 查找 + Key 算法")

    # 传入连接参数，config 对象可选
    with Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as ss:
        ss.set_key_calculator(my_key_calculator)

        ss.change_session(0x03)
        ss.change_level(0x01)
        # print(ss >> '22DC06')

    # 自定义配置示例
    # with Service(
    #     ip='198.18.44.1',
    #     ecus={'mcu': ('198.18.44.49', 0x1301)},
    #     doip=DoIPConfig(recv_timeout=5.0),
    #     retry=RetryConfig(count=5),
    # ) as ss:
    #     ...