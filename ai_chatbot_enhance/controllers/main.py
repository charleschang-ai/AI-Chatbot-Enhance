from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


class AIChatController(http.Controller):  # 用你原来的类名

    @http.route(["/ai/get_ai_response"], type="json", auth="public")
    def get_ai_response(self, mail_message_id, channel_id, **kwargs):
        channel = self._get_ai_channel_from_id(channel_id)
        if not channel:
            raise NotFound()
        message = self._get_message_in_channel(mail_message_id, channel)
        if message:
            channel.sudo().ai_agent_id._get_response_for_channel(message, channel)

    def _get_ai_channel_from_id(self, channel_id):
        channel = request.env['discuss.channel'].sudo().browse(int(channel_id)).exists()
        if channel and channel.ai_agent_id:
            return channel
        return request.env['discuss.channel'].sudo()

    def _get_message_in_channel(self, message_id, channel):
        """ 17 没有 _get_with_access：直接 sudo 取消息，
            并校验它确实属于这个 AI 频道（这是这里有意义的访问校验）。"""
        message = request.env['mail.message'].sudo().browse(int(message_id)).exists()
        if message and message.model == 'discuss.channel' and message.res_id == channel.id:
            return message
        return request.env['mail.message'].sudo()

    @http.route('/ai/close_chat_ai', methods=["POST"], type="json", auth='public')
    def close_ai_chat(self, channel_id):
        channel = self._get_ai_channel_from_id(channel_id)
        if channel and len(channel) < 6:
            channel.sudo().unlink()