from odoo import fields, models


class AITopic(models.Model):
    _name = 'ai.topic'
    _description = "Create tools to direct Odoo AI in assisting the user with their tasks."

    name = fields.Char(string="Title", required=True)
    description = fields.Text(string="Description")
    instructions = fields.Text(string="Instructions")
    tool_ids = fields.Many2many('ir.actions.server', string="AI Tools", domain=[('use_in_ai', '=', True)],
                                groups='base.group_system')
