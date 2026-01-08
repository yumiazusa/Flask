# config_template.py - 配置模板
"""
复制此文件为config.py，然后修改配置
"""

class Config:
    # Flask安全密钥（生产环境请使用复杂字符串）
    SECRET_KEY = 'your-secret-key-change-this-in-production'

    # MySQL配置（根据实际情况修改）
    MYSQL_HOST = '47.108.254.13'  # MySQL服务器地址
    MYSQL_PORT = 3306  # MySQL端口，默认3306
    MYSQL_DATABASE = 'projectDB'  # 数据库名
    MYSQL_USERNAME = 'ProjectDB'  # 用户名
    MYSQL_PASSWORD = '4100282Ly@'  # 密码
    MYSQL_CHARSET = 'utf8mb4'  # 字符集

    # 项目号配置
    PROJECT_YEAR = '2026'  # 固定年份部分

    # 评估类型映射（类型名称 -> 三字母代码）
    EVALUATION_TYPES = {
        '资产评估': 'AAP',
        '土地评估': 'LAP',
        '珠宝评估': 'JAP',
        '矿业权评估': 'MRV',
        '咨询': 'ACP'
    }

    # Flask配置
    DEBUG = True
    HOST = '0.0.0.0'
    PORT = 5000


config = Config()