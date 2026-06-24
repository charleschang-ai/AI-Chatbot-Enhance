from odoo import fields, models, _, api
from odoo.exceptions import UserError

INTERFACE_KEYS = [
    ("html_field_record", "Write in an HTML field"),
    ("mail_composer", "Write an email"),
    ("html_field_text_select", "Rewrite content"),
    ("chatter_ai_button", "Get help on a record"),
    ("html_prompt_shortcut", "Convert a prompt in an email"),
    ("systray_ai_button", "Ask AI for help"),
    ("voice_transcription_component", "Summary Buttons for Voice Transcription Component")
]


class AIComposer(models.Model):
    _name = "ai.composer"
    _description = "AI model configurations (system prompts) for text drafting."

    def _get_default_agent(self):
        return self.env["ir.model.data"]._xmlid_to_res_id("ai.ai_default_agent")

    name = fields.Char(
        "Rule Name", help="The identifier for the interface component to agent rule", required=True,
    )
    interface_key = fields.Selection(selection=INTERFACE_KEYS, string="Action", required=True)
    focused_models = fields.Many2many('ir.model', string="Models")
    ai_agent = fields.Many2one('ai.agent', string="Agent", default=_get_default_agent)
    default_prompt = fields.Text("Instructions")
    is_system_default = fields.Boolean('Is the rule a system default or user created', default=False, readonly=True, copy=False)
    available_prompts = fields.One2many('ai.prompt.button', 'composer_id', string="Available User Prompts")

    @api.ondelete(at_uninstall=False)
    def _unlink_except_default_rules(self):
        if any(rule.is_system_default for rule in self):
            raise UserError(self.env._('System default prompts cannot be removed.'))

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        if 'name' not in default:
            for composer, vals in zip(self, vals_list):
                vals['name'] = _("%s (copy)", composer.name)
        return vals_list

    @api.model
    def get_prompts_by_agent(self, agent_id):
        """根据 ai_agent_id 返回提示按钮列表 [{id, text}]"""
        composer = self.search([('ai_agent', '=', agent_id)], limit=1)
        if not composer:
            return []
        buttons = composer.available_prompts.sorted('sequence')
        return [{'id': btn.id, 'text': btn.name} for btn in buttons]
