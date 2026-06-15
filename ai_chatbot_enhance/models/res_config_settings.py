from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    openai_key_enabled = fields.Boolean(
        string="Enable custom OpenAI API key",
        compute='_compute_openai_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    openai_key = fields.Char(
        string="OpenAI API key",
        config_parameter='ai_chatbot_enhance.openai_key',
        readonly=False,
        groups='base.group_system',
    )

    google_key_enabled = fields.Boolean(
        string="Enable custom Google API key",
        compute='_compute_google_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    google_key = fields.Char(
        string="Google AI API key",
        config_parameter='ai_chatbot_enhance.google_key',
        readonly=False,
        groups='base.group_system',
    )

    deepseek_key_enabled = fields.Boolean(
        string="Enable DeepSeek API key",
        compute='_compute_deepseek_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    deepseek_key = fields.Char(
        string="DeepSeek API key",
        config_parameter='ai_chatbot_enhance.deepseek_key',
        readonly=False,
        groups='base.group_system',
    )

    qwen_key_enabled = fields.Boolean(
        string="Enable Qwen API key",
        compute='_compute_qwen_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    qwen_key = fields.Char(
        string="Qwen API key",
        config_parameter='ai_chatbot_enhance.qwen_key',
        readonly=False,
        groups='base.group_system',
    )

    def _compute_openai_key_enabled(self):
        for record in self:
            record.openai_key_enabled = bool(record.openai_key)

    def _compute_google_key_enabled(self):
        for record in self:
            record.google_key_enabled = bool(record.google_key)

    def _compute_deepseek_key_enabled(self):
        for record in self:
            record.deepseek_key_enabled = bool(record.deepseek_key)

    def _compute_qwen_key_enabled(self):
        for record in self:
            record.qwen_key_enabled = bool(record.qwen_key)
