import os
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
        
        # 1. 记忆暂存区 (Working Memory) - 内存存储
        self.working_memory = {} # session_id -> content
        
        # 启动清理任务
        self._setup_cleanup_task()

    @property
    def data_dir(self) -> Path:
        """延迟获取并创建数据目录"""
        if self._data_dir is None:
            try:
                self._data_dir = StarTools.get_data_dir()
            except Exception:
                # 安全回退
                self._data_dir = Path.cwd() / "data" / "plugins" / "thoughts"
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

    def _setup_cleanup_task(self):
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

        async def cleanup_loop():
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
        
        asyncio.create_task(cleanup_loop())

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        uid = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(uid)
        
        if curr_cid is None:
            return
            
        session_id = f"{uid}_{curr_cid}"
        
        # 检查是否是新对话（清理旧的暂存区）
        is_new = False
        conversation: Conversation = await conv_mgr.get_conversation(uid, curr_cid)
        if not conversation or not conversation.history or json.loads(conversation.history) == []:
            is_new = True
        
        if is_new and session_id in self.working_memory:
            del self.working_memory[session_id]
            logger.info(f"[Thoughts] 新对话开启，清理暂存区: {session_id}")

        injected_parts = []
        
        # 注入暂存区内容
        if session_id in self.working_memory:
            wm_content = self.working_memory[session_id]
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
        instruction = "【提示 - 记忆暂存】\n你可以在回复中使用 `[暂存]内容[暂存结束]` 来将内容存入工作记忆暂存区。暂存内容不会被聊天对象看到且不计入历史，但在下次对话时会提供给你。支持多块暂存。"
        req.system_prompt += instruction
        injected_parts.append(instruction)
            
        if injected_parts:
            logger.debug(f"[Thoughts] 插件注入内容:\n{''.join(injected_parts)}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """拦截 AI 输出，提取 [暂存]...[暂存结束] 内容"""
        if not resp or not resp.completion_text:
            return
            
        resp_text = resp.completion_text
        
        # 正则匹配 [暂存]...[暂存结束]
        pattern = r'\[暂存\](.*?)\[暂存结束\]'
        matches = re.findall(pattern, resp_text, re.DOTALL)
        
        if matches:
            # 提取并合并所有暂存块内容
            wm_content = "\n".join([m.strip() for m in matches if m.strip()])
            
            if wm_content:
                uid = event.unified_msg_origin
                conv_mgr = self.context.conversation_manager
                curr_cid = await conv_mgr.get_curr_conversation_id(uid)
                session_id = f"{uid}_{curr_cid}"
                
                self.working_memory[session_id] = wm_content
                logger.info(f"[Thoughts] 更新暂存区 ({session_id}): {wm_content}")
            
            # 移除所有暂存块，确保不计入历史记录且不展示给用户
            new_text = re.sub(pattern, '', resp_text, flags=re.DOTALL).strip()
            resp.completion_text = new_text

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

    @llm_tool(name="record_private_thought")
    async def record_private_thought(self, event: AstrMessageEvent, thought: str) -> str:
        """将你的私人思考记录到聊天历史中。请仅当你有想长期记住且不想公开的内心想法时使用。这些内容不会被聊天对象看到，但会在后续对话中作为上下文提供给你。
        
        Args:
            thought(string): 你的思考内容。
        """
        if not thought:
            return "错误：思考内容不能为空。"
        
        uid = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(uid)
        
        if curr_cid:
            try:
                conversation: Conversation = await conv_mgr.get_conversation(uid, curr_cid)
                history = json.loads(conversation.history) if conversation.history else []
                
                # 以 assistant 角色存入历史，标记为私人思考
                history.append({
                    "role": "assistant",
                    "content": f"【私人思考记录】: {thought}"
                })
                
                await conv_mgr.update_conversation_history(uid, curr_cid, json.dumps(history, ensure_ascii=False))
                return "思考已记录到私密历史中。"
            except Exception as e:
                logger.error(f"[Thoughts] 记录私人思考失败: {e}")
                return f"错误：记录失败 {e}"
        
        return "错误：未能获取当前会话 ID。"
