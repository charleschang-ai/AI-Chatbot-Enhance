import logging
from werkzeug.exceptions import NotFound
from odoo import http
from odoo.addons.mail.tools.discuss import add_guest_to_context
from odoo.addons.mail.controllers.thread import ThreadController

_logger = logging.getLogger(__name__)


class AIController(ThreadController):

    @http.route(["/ai/get_ai_response"], type="jsonrpc", auth="public")
    # @add_guest_to_context
    def get_ai_response(self, mail_message_id, channel_id, **kwargs):
        channel = self._get_ai_channel_from_id(channel_id)
        if not channel:
            raise NotFound()
        message = self._get_message_with_access(mail_message_id)
        if message:
            channel.sudo().ai_agent_id.with_context()._get_response_for_channel(message, channel)

    def _get_ai_channel_from_id(self, channel_id):
        channel = self.env['discuss.channel'].search([('id', '=', channel_id)])
        if channel.sudo().ai_agent_id:
            return channel
        return self.env['discuss.channel']

    @http.route('/ai/close_chat_ai', methods=["POST"], type="jsonrpc", auth='public')
    @add_guest_to_context
    def close_ai_chat(self, channel_id):
        channel = self._get_ai_channel_from_id(channel_id)
        if channel and len(channel) < 6:
            channel.sudo().unlink()
