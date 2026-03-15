import json
import re
import asyncio
from datetime import datetime
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, llm_tool
from astrbot.api.provider import ProviderRequest
from astrbot.core.conversation_mgr import Conversation

@register("astrbot_plugin_thoughts", "olozhika", "轻量级时效性记忆插件", "1.0.0")
class ThoughtsPlugin(Star):
    def __init__(self, context: Context, config: any = None):
        super().__init__(context)
        self.config = config if config else {}
        self._data_dir = None
        self._interim_memory = None
        self._cleanup_task = None
        
        # 1. 记忆暂存区 (Working Memory) - 内存存储
        self.working_memory = {} # uid (UMO) -> content
        
        # 启动清理任务 (延迟到事件循环运行时)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                self._cleanup_task = loop.create_task(self._setup_cleanup_task())
        except RuntimeError:
            # 如果当前没有运行中的事件循环，则不在此处启动
            pass

    @property
    def data_dir(self) -> Path:
        """延迟获取并创建数据目录"""
        if self._data_dir is None:
            try:
                self._data_dir = StarTools.get_data_dir()
            except Exception:
                # 规范化回退路径：data/plugin_data/astrbot_plugin_thoughts
                self._data_dir = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_thoughts"
                logger.warning(f"[Thoughts] 无法通过 StarTools 获取数据目录，回退至规范路径: {self._data_dir}")
            self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    @property
    def interim_memory(self) -> list:
        """延迟加载中期记忆"""
        if self._interim_memory is None:
            file_path = self.data_dir / "interim_memory.json"
            if file_path.exists():
                try:
                    self._interim_memory = json.loads(file_path.read_text(encoding='utf-8'))
                except Exception as e:
                    logger.error(f"[Thoughts] 加载中期记忆失败: {e}")
                    self._interim_memory = []
            else:
                self._interim_memory = []
        return self._interim_memory

    @interim_memory.setter
    def interim_memory(self, value: list):
        self._interim_memory = value

    def _save_interim_memory(self):
        if self._interim_memory is None:
            return
        try:
            file_path = self.data_dir / "interim_memory.json"
            file_path.write_text(json.dumps(self._interim_memory, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.error(f"[Thoughts] 保存中期记忆失败: {e}")

    async def _setup_cleanup_task(self):
        def match_cron(cron_str, dt):
            parts = cron_str.split()
            if len(parts) != 5: return False
            
            m, h, dom, mon, dow = parts
            # cron dow: 0-6 (Sun-Sat), python weekday: 0-6 (Mon-Sun)
            # Adjusting python weekday to cron: (dt.weekday() + 1) % 7
            cron_dow_val = (dt.weekday() + 1) % 7
            
            def match_part(part, val):
                if part == '*': return True
                try:
                    return int(part) == val
                except:
                    return False
            
            return (match_part(m, dt.minute) and 
                    match_part(h, dt.hour) and 
                    match_part(dom, dt.day) and 
                    match_part(mon, dt.month) and 
                    match_part(dow, cron_dow_val))

        while True:
            try:
                cron_str = self.config.get("interim_memory_cleanup_time", "55 23 * * *")
                now = datetime.now()
                
                if match_cron(cron_str, now):
                    logger.info(f"[Thoughts] 到达清理时间 {cron_str}，清空中期记忆。")
                    self.interim_memory = []
                    self._save_interim_memory()
                    await asyncio.sleep(61) # 防止在一分钟内重复触发
            except Exception as e:
                logger.error(f"[Thoughts] 清理任务出错: {e}")
            await asyncio.sleep(30)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 确保清理任务在运行
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._setup_cleanup_task())
            except RuntimeError:
                pass

        uid = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(uid)
        
        # 保存当前的请求内容，以便在工具调用时记录完整历史 (参考 silent_response)
        event.llm_req_prompt = req.prompt
        if hasattr(req, "extra_user_content_parts"):
            event.extra_user_content_parts = req.extra_user_content_parts
        
        if curr_cid is None:
            return
            
        # 检查是否是新对话（清理旧的暂存区）
        is_new = False
        conversation: Conversation = await conv_mgr.get_conversation(uid, curr_cid)
        if conversation and conversation.history:
            try:
                history = json.loads(conversation.history)
                if not history:
                    is_new = True
            except json.JSONDecodeError:
                logger.warning(f"[Thoughts] 会话 {curr_cid} 历史记录 JSON 解析失败，按空历史处理。")
                is_new = True
        else:
            is_new = True
        
        if is_new and uid in self.working_memory:
            del self.working_memory[uid]
            logger.info(f"[Thoughts] 新对话开启，清理暂存区 (UMO): {uid}")

        injected_parts = []
        
        # 注入暂存区内容
        if uid in self.working_memory:
            wm_content = self.working_memory[uid]
            title = self.config.get("working_memory_prompt", "【工作记忆暂存区 - 这是你之前暂存的想法或待办】")
            part = f"\n\n{title}\n{wm_content}\n"
            req.system_prompt += part
            injected_parts.append(part)

        # 注入中期记忆
        if self.interim_memory:
            im_text = "\n".join([f"- {m}" for m in self.interim_memory])
            title = self.config.get("interim_memory_prompt", "【跨对话中期记忆 - 这些是你近期记录的重要经历】")
            part = f"{title}{im_text}\n"
            req.system_prompt += part
            injected_parts.append(part)
            
        # 注入格式说明（确保 AI 知道新格式）
        instruction = "【提示 - 记忆暂存与私密思考】\n1. 你可以在回复中使用 `[暂存]内容[暂存结束]` 来将内容存入工作记忆暂存区。暂存内容不会被聊天对象看到且不计入历史，但在下次对话时会提供给你。支持多块暂存。如需清空暂存区，请包含 `[清空暂存]`。\n2. 你可以在回复中使用 `[私密思考]内容[私密思考结束]` 来记录你的内心想法。这些内容会被记录在对话历史中供你后续参考，但不会发送给聊天对象。请仅在需要长期记住某些私密信息时使用。"
        req.system_prompt += instruction
        injected_parts.append(instruction)
            
        if injected_parts:
            logger.debug(f"[Thoughts] 插件注入内容:\n{''.join(injected_parts)}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """拦截 AI 输出，处理暂存和私密思考。
        为了让历史记录包含私密思考而用户看不到，我们手动发送过滤后的消息并阻止默认发送。
        """
        if not resp or not resp.completion_text:
            return
            
        full_text = resp.completion_text
        resp_text = full_text
        uid = event.unified_msg_origin
        
        # 1. 处理 [清空暂存]
        if "[清空暂存]" in resp_text:
            if uid in self.working_memory:
                del self.working_memory[uid]
                logger.info(f"[Thoughts] AI 请求清空暂存区 (UMO: {uid})")
            resp_text = resp_text.replace("[清空暂存]", "").strip()
        
        # 2. 处理 [暂存]...[暂存结束]
        pattern_wm = r'\[暂存\](.*?)\[暂存结束\]'
        matches_wm = re.findall(pattern_wm, resp_text, re.DOTALL)
        if matches_wm:
            wm_content = "\n".join([m.strip() for m in matches_wm if m.strip()])
            if wm_content:
                self.working_memory[uid] = wm_content
                logger.info(f"[Thoughts] 更新暂存区 (UMO: {uid}): {wm_content}")
            resp_text = re.sub(pattern_wm, '', resp_text, flags=re.DOTALL).strip()

        # 3. 处理 [私密思考]...[私密思考结束]
        # 我们只在发送给用户的文本中移除它，但保留在 resp.completion_text 中以供框架存入历史
        pattern_pt = r'\[私密思考\](.*?)\[私密思考结束\]'
        has_private_thought = re.search(pattern_pt, resp_text, re.DOTALL)
        
        if has_private_thought:
            # 过滤掉私密思考内容用于发送给用户
            display_text = re.sub(pattern_pt, '', resp_text, flags=re.DOTALL).strip()
            
            # 1. 关键：修改 resp.completion_text 为过滤后的内容
            # 这样即使 stop_event() 没能阻止框架发送，发出去的也是过滤后的内容
            resp.completion_text = display_text
            
            # 2. 手动将完整内容（包含私密思考）保存到历史记录
            conv_mgr = self.context.conversation_manager
            curr_cid = await conv_mgr.get_curr_conversation_id(uid)
            if curr_cid:
                try:
                    # 构造当前这一轮的对话对
                    user_content = []
                    prompt = getattr(event, "llm_req_prompt", "")
                    if prompt:
                        user_content.append({"type": "text", "text": prompt})
                    
                    req_parts = getattr(event, "extra_user_content_parts", [])
                    if req_parts:
                        for part in req_parts:
                            if hasattr(part, "text"):
                                user_content.append({"type": "text", "text": part.text})
                            elif isinstance(part, dict):
                                user_content.append(part)
                    
                    if not user_content:
                        user_content.append({"type": "text", "text": event.message_str})

                    user_message = {"role": "user", "content": user_content}
                    # 助手消息：使用 full_text (包含私密思考标签的原始输出)
                    assistant_message = {
                        "role": "assistant",
                        "content": [{"type": "text", "text": full_text}]
                    }
                    
                    await conv_mgr.add_message_pair(curr_cid, user_message, assistant_message)
                    logger.info(f"[Thoughts] 已手动将完整回复（含私密思考）存入历史记录。")
                except Exception as e:
                    logger.error(f"[Thoughts] 手动记录历史失败: {e}")

            # 3. 手动发送过滤后的内容给用户
            if display_text:
                await event.send(display_text)
                logger.info(f"[Thoughts] 已手动向用户发送过滤后的回复。")
            
            # 4. 停止事件，防止框架重复记录历史（记录过滤后的版本）以及重复发送
            event.stop_event()
        else:
            # 如果没有私密思考，按正常流程走（resp_text 可能处理过暂存标签）
            resp.completion_text = resp_text

    @llm_tool(name="record_interim_memory")
    async def record_interim_memory(self, event: AstrMessageEvent, content: str) -> str:
        """记录重要事件或知识，这些内容会在不同的对话线程间共享，直到被定时清理。当你学到新东西或发生跨对话的重要事件时使用。
        
        Args:
            content(string): 要记录的内容。
        """
        if not content:
            return "错误：内容不能为空。"
        
        self.interim_memory.append(content)
        self._save_interim_memory()
        return f"已记录中期记忆：{content}"

