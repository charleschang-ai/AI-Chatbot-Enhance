import logging
from werkzeug.exceptions import NotFound
from odoo import http
from odoo.http import request
# from odoo.addons.mail.tools.discuss import add_guest_to_context
# from odoo.addons.mail.controllers.thread import ThreadController

_logger = logging.getLogger(__name__)


class AIController(http.Controller):

    @http.route(["/ai/get_ai_response"], type="json", auth="public")
    # @add_guest_to_context
    def get_ai_response(self, mail_message_id, channel_id, **kwargs):
        channel = self._get_ai_channel_from_id(channel_id)
        if not channel:
            raise NotFound()
        message = self._get_message_with_access(mail_message_id)
        if message:
            channel.sudo().ai_agent_id.with_context()._get_response_for_channel(message, channel)

    def _get_ai_channel_from_id(self, channel_id):
        channel = request.env['discuss.channel'].search([('id', '=', channel_id)])
        if channel.sudo().ai_agent_id:
            return channel
        return request.env['discuss.channel']

    @http.route('/ai/close_chat_ai', methods=["POST"], type="json", auth='public')
    def close_ai_chat(self, channel_id):
        channel = self._get_ai_channel_from_id(channel_id)
        if channel and len(channel) < 6:
            channel.sudo().unlink()

    @classmethod
    def _get_message_with_access(cls, message_id, mode="read", **kwargs):
        """ Simplified getter that filters access params only, making model methods
        using strong parameters. """
        message_su = request.env['mail.message'].sudo().browse(message_id).exists()
        if not message_su:
            return message_su
        return request.env['mail.message']._get_with_access(message_su.id, "read", **{
                key: value for key, value in kwargs.items()
                if key in request.env[message_su.model or 'mail.thread'].set()
            },)