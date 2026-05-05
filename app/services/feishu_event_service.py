"""飞书长连接事件服务 - 处理卡片回调

使用飞书官方 SDK (lark-oapi) 建立长连接，接收卡片回调事件。
"""

import asyncio
import json
import logging
import threading
from typing import Any

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from app.config import settings
from app.services import feishu_base_service

logger = logging.getLogger(__name__)

# 长连接客户端实例
_ws_client = None
_event_loop = None
_running = False
_ws_thread = None


def do_card_action_trigger(data: P2CardActionTrigger) -> dict:
    """处理卡片回传交互事件

    飞书卡片回调入口函数，由 SDK 自动调用。
    """
    try:
        logger.info("收到卡片回调: %s", lark.JSON.marshal(data))

        # 解析回调数据
        token_data = data.token or {}
        action_value = data.action.value if data.action else {}

        # 获取 chat_id
        chat_id = token_data.get("chat_id") or token_data.get("open_chat_id")

        # 获取表单数据
        form_value = action_value.get("form_value", {})
        action_type = action_value.get("action")

        logger.info("卡片回调解析: action=%s, chat_id=%s, form=%s", action_type, chat_id, form_value)

        # 在异步上下文中处理
        if _event_loop and asyncio.iscoroutinefunction(_handle_card_callback_async):
            # 在已有事件循环中执行
            future = asyncio.run_coroutine_threadsafe(
                _handle_card_callback_async(chat_id, action_type, form_value),
                _event_loop
            )
            # 等待结果（最多 2 秒）
            try:
                result = future.result(timeout=2.0)
                return result
            except Exception as e:
                logger.error("卡片回调处理超时或失败: %s", e)
                return {
                    "toast": {
                        "type": "error",
                        "content": "处理超时，请重试"
                    }
                }
        else:
            # 同步处理（简化版）
            return _handle_card_callback_sync(chat_id, action_type, form_value)

    except Exception as e:
        logger.error("卡片回调处理异常: %s", e)
        return {
            "toast": {
                "type": "error",
                "content": f"处理失败: {str(e)}"
            }
        }


def _handle_card_callback_sync(chat_id: str, action_type: str, form_value: dict) -> dict:
    """同步处理卡片回调（简化版，不调用异步函数）

    注意：此版本无法调用需要 async 的函数（如 verify_folder_token）
    """
    folder_token = form_value.get("folder_token", "")

    if folder_token and action_type == "confirm_token":
        # 直接保存 token，不验证（简化处理）
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop and not loop.is_closed():
                # 在现有循环中执行
                future = asyncio.run_coroutine_threadsafe(
                    feishu_base_service.set_user_folder(chat_id, folder_token),
                    loop
                )
                future.result(timeout=1.0)
            else:
                logger.error("无法获取事件循环，跳过保存")
        except Exception as e:
            logger.error("保存文件夹配置失败: %s", e)

        return {
            "toast": {
                "type": "success",
                "content": f"✓ 文件夹配置成功！\n\nfolder_token: {folder_token}\n\n多维表格将创建在该文件夹下。"
            }
        }

    return {"toast": {"type": "info", "content": "已收到"}}


async def _handle_card_callback_async(chat_id: str, action_type: str, form_value: dict) -> dict:
    """异步处理卡片回调（完整版，支持验证）

    此函数在主事件循环中执行，可以调用异步函数。
    """
    folder_token = form_value.get("folder_token", "")

    if folder_token and action_type == "confirm_token":
        # 验证并保存文件夹 token
        if await feishu_base_service.verify_folder_token(folder_token):
            await feishu_base_service.set_user_folder(chat_id, folder_token)
            return {
                "toast": {
                    "type": "success",
                    "content": f"✓ 文件夹配置成功！\n\n多维表格将创建在该文件夹下。"
                }
            }
        else:
            return {
                "toast": {
                    "type": "error",
                    "content": "文件夹 token 无效，请检查后重试"
                }
            }

    return {"toast": {"type": "info", "content": "已收到"}}


def create_event_handler():
    """创建飞书事件处理器

    注册卡片回调处理函数，以及空处理器来消除其他事件的错误日志。
    """
    def handle_message_receive_v1(data):
        """处理消息接收事件（空实现，消息由 bridge.py 处理）"""
        logger.debug("收到消息事件（由 bridge.py 处理）")
        return None

    def handle_message_read_v1(data):
        """处理消息已读事件（空实现）"""
        logger.debug("收到消息已读事件")
        return None

    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_card_action_trigger(do_card_action_trigger) \
        .register_p1_im_message_receive_v1(handle_message_receive_v1) \
        .register_p1_im_message_message_read_v1(handle_message_read_v1) \
        .build()

    logger.info("飞书事件处理器创建成功")
    return handler


def start_ws_client_sync():
    """启动飞书长连接客户端（同步阻塞）

    此函数会阻塞当前线程，需要在单独的线程中运行。
    """
    global _ws_client, _running

    app_id = getattr(settings, "feishu_app_id", "")
    app_secret = getattr(settings, "feishu_app_secret", "")

    if not app_id or not app_secret:
        logger.error("飞书 app_id 或 app_secret 未配置，无法启动长连接")
        return

    logger.info("启动飞书长连接客户端: app_id=%s", app_id)

    event_handler = create_event_handler()

    try:
        # 创建长连接客户端
        _ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO
        )

        _running = True

        # 启动客户端（阻塞）
        logger.info("飞书长连接正在连接...")
        _ws_client.start()

    except Exception as e:
        logger.error("飞书长连接启动失败: %s", e)
        _running = False


async def start_event_listener(event_loop=None):
    """启动飞书长连接事件监听

    在单独的线程中运行长连接客户端，避免阻塞主事件循环。

    Args:
        event_loop: 当前事件循环，用于异步回调处理
    """
    global _event_loop, _running

    if _running:
        logger.warning("飞书长连接已在运行")
        return

    _event_loop = event_loop or asyncio.get_event_loop()

    # 在单独线程中运行长连接客户端
    thread = threading.Thread(target=start_ws_client_sync, daemon=True, name="feishu-ws-client")
    thread.start()

    # 等待连接启动
    await asyncio.sleep(2)

    if _running:
        logger.info("飞书长连接事件监听器已启动")
    else:
        logger.error("飞书长连接启动失败")


async def stop_event_listener():
    """停止飞书长连接事件监听"""
    global _running, _ws_client

    _running = False

    if _ws_client:
        try:
            # lark-oapi SDK 的客户端没有提供 stop 方法
            # 只能设置标志位，线程会在下次检查时退出
            logger.info("飞书长连接标志位已清除")
        except Exception as e:
            logger.error("停止长连接时出错: %s", e)

    logger.info("飞书长连接事件监听器已停止")


def is_running() -> bool:
    """检查长连接是否正在运行"""
    return _running
