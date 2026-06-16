# Part of custom AI module.
import base64
from datetime import datetime, timedelta
import logging
from ast import literal_eval
from collections import defaultdict

from lxml import etree

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import file_open
from odoo.tools.misc import mute_logger, submap
from odoo.tools import file_open, html_sanitize, SQL, is_html_empty, ormcache
from odoo.tools.mail import html_to_inner_content
from ..utils.llm_api_service import LLMApiService
from textwrap import dedent
from ..utils.ai_citation import apply_numeric_citations, get_attachment_ids_from_text


try:
    from markdown2 import markdown
except ImportError:
    markdown = None

from ..utils.llm_providers import (
    PROVIDERS,
    get_provider,

)

_logger = logging.getLogger(__name__)

TEMPERATURE_MAP = {
    'analytical': 0.1,
    'balanced': 0.5,
    'creative': 0.9,
}

PREPROMPTS = {
    'default_system_prompt': "You are a RAG assistant.",
    'tools': dedent("""
        You have access to tools that can perform actions. Only use these tools when:
        1. The user explicitly requests the action.
        2. The action is clearly the most appropriate response to their query.

        If the user asks you to perform an action, retrieve the required information from his prompt and the conversation history, then use the tool.

        If date is needed, Use today's date to make a relative date if the user didn't provide a clear one (e.g. tomorrow, in one week, etc.).
        Rarely suggest the actions in your response.
    """).strip(),
    'restrict_to_sources': dedent("""
        ## INSTRUCTIONS FOR ANSWERING QUERIES

        1. For greetings (hello, hi, how are you), reply with a greeting.

        2. For all other questions, you MUST ONLY use information from the provided context and conversation history.

        3. If the RAG context and history don't contain information to answer the query or it is not provided at all:
           - use the assistant/user messages as context or ask the user to provide more information.
           - DO NOT make up information or use your general knowledge.

        4. When answering based on the context:
           - Synthesize information from multiple sources when appropriate
           - Consider the conversation history for follow-up questions

        5. If a user asks a follow-up question like 'what is this?' or 'tell me more', refer to the conversation history to understand the context and answer accordingly.

        6. If no context is provided at all, respond with: 'No source information has been provided for me to reference.'

        7. Avoid using HTML elements in your response.
    """).strip(),
    'context': dedent("""
        - Use the RAG context to answer the question.
        - Every claim or piece of information provided in the answer **MUST** be immediately followed by an inline citation in the format **[SOURCE:Attachment ID]** to indicate its source from the RAG context (e.g., "The capital of France is Paris [SOURCE:210].").
        - If a claim draws on multiple sources, cite all of them (e.g., "The process requires heat and pressure [SOURCE:210, 211].").
        - If **NO** source chunks were used to answer the question, do not include any citations.

        - Example of the required format for the response with the attachment IDs [SOURCE:210, 211] in its answer:
        - The primary goal of the project is to enhance data security protocols [SOURCE:210]. This enhancement includes a mandatory two-factor authentication system [SOURCE:211].
    """).strip(),
}


def clean_search_view_xml(search_view_arch):
    """Clean and restructure search view XML for AI consumption."""
    if not search_view_arch:
        return ""

    # Parse XML
    tree = etree.fromstring(search_view_arch)

    # Create new clean structure
    clean_tree = etree.Element("search")

    # 1. Add searchable fields (excluding only those with invisible="1")
    searchable_fields_elem = etree.SubElement(clean_tree, "searchable_fields")
    for field in tree.xpath(".//field[@name and not(@invisible='1') and not(ancestor::group)]"):
        # Copy only essential attributes
        clean_field = etree.SubElement(searchable_fields_elem, "field")
        for attr in ["name", "string", "filter_domain", "operator"]:
            if field.get(attr):
                clean_field.set(attr, field.get(attr))

    # 2. Add filters grouped by separators (excluding those with invisible="1")
    filters_elem = etree.SubElement(clean_tree, "filters")

    # Process filters in groups separated by separators
    current_group = None
    for elem in tree:
        if elem.tag == "separator":
            # Start a new group on separator
            current_group = None
        elif elem.tag == "filter" and elem.get("name") and elem.get("invisible") != "1":
            # Skip filters that are inside <group> elements (those are groupbys)
            if elem.getparent().tag != "group":
                if current_group is None:
                    current_group = etree.SubElement(filters_elem, "group")
                clean_filter = etree.SubElement(current_group, "filter")
                for attr in ["name", "string", "domain", "date"]:
                    if elem.get(attr):
                        clean_filter.set(attr, elem.get(attr))

    # 3. Add groupby filters with extracted field information
    groupbys_elem = etree.SubElement(clean_tree, "groupbys")
    for group in tree.xpath(".//group"):
        for filter_elem in group.xpath(".//filter[@name and not(@invisible='1')]"):
            if filter_elem.get("context") and "group_by" in filter_elem.get("context"):
                clean_filter = etree.SubElement(groupbys_elem, "filter")
                clean_filter.set("name", filter_elem.get("name"))
                if filter_elem.get("string"):
                    clean_filter.set("string", filter_elem.get("string"))

                # Extract the actual field name from the context
                context_str = filter_elem.get("context")
                context_dict = literal_eval(context_str)
                if "group_by" in context_dict:
                    clean_filter.set("group_by_field", context_dict["group_by"])

    # Return compact XML string
    return etree.tostring(clean_tree, encoding="unicode", pretty_print=False)


class AIAgent(models.Model):
    _name = 'ai.agent'
    _description = "AI Agent"
    _order = 'name'

    @api.model
    def _get_llm_model_selection(self):
        selection = []
        for provider in PROVIDERS:
            selection.extend(provider.llms)
        return selection

    active = fields.Boolean(default=True)
    name = fields.Char(string="Agent Name", related='partner_id.name', required=True, readonly=False, store=True)
    subtitle = fields.Char(string="Description")
    system_prompt = fields.Text(string="System Prompt", help="Customize to control relevance and formatting.")
    response_style = fields.Selection(
        selection=[
            ('analytical', "Analytical"),
            ('balanced', "Balanced"),
            ('creative', "Creative"),
        ],
        string="Response Style",
        default='balanced',
        required=True,
    )
    restrict_to_sources = fields.Boolean(
        string="Restrict to Sources",
        help="If checked, the agent will only respond based on the provided sources.")

    llm_model = fields.Selection(
        selection=_get_llm_model_selection,
        string="LLM Model",
        default='deepseek-v4-flash',
        required=True,
    )
    image_128 = fields.Image("Image", related="partner_id.image_1920", max_width=128, max_height=128, readonly=False)
    avatar_128 = fields.Image("Avatar", related="partner_id.avatar_128")
    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)

    is_system_agent = fields.Boolean('System Agent', default=False)

    enable_web_search = fields.Boolean(
        string="Enable internet search",
        default=True,
        help="(Only OpenAI / Gemini supported) Allows the Agent to access "
             "the internet to retrieve the latest information."
    )

    sources_ids = fields.One2many(
        'ai.agent.source',
        'agent_id',
        string="Sources",
    )
    sources_fully_processed = fields.Boolean(compute="_compute_sources_fully_processed", default=True)

    topic_ids = fields.Many2many(
        'ai.topic',
        string="Topics",
        help="A topic includes instructions and tools that guide Odoo AI in helping the user complete their tasks.",
    )

    is_ask_ai_agent = fields.Boolean(
        'Is Natural Language Query Agent',
        compute='_compute_is_ask_ai_agent',
        search='_search_is_ask_ai_agent'
    )

    @api.model
    def _get_ask_ai_topics(self):
        return [t for t in (
            self.env.ref('ai_chatbot_enhance.ai_topic_natural_language_query', raise_if_not_found=False),
            self.env.ref('ai_chatbot_enhance.ai_topic_information_retrieval_query', raise_if_not_found=False)
        ) if t]

    def _compute_is_ask_ai_agent(self):
        ask_ai_topics = self._get_ask_ai_topics()
        for agent in self:
            agent.is_ask_ai_agent = bool(set(agent.topic_ids) & set(ask_ai_topics))

    def _search_is_ask_ai_agent(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Invalid search operator."))

        if ask_ai_topics := self._get_ask_ai_topics():
            if operator == '=' and value or operator == '!=' and not value:  # truthy
                return [("topic_ids", "in", [t.id for t in ask_ai_topics])]
            elif operator == '=' and not value or operator == '!=' and value:  # falsy
                return [("topic_ids", "not in", [t.id for t in ask_ai_topics])]
        else:
            return [('id', '=', False)]

    @api.model_create_multi
    def create(self, vals_list):
        with file_open('ai_chatbot_enhance/static/description/icon.jpg', 'rb') as f:
            image_placeholder = f.read()
        for vals in vals_list:
            # check_model_depreciation(self.env, vals.get("llm_model"))
            partner = self.env['res.partner'].create({'name': vals.get('name'), 'active': False, })
            vals['partner_id'] = partner.id
        ai_agents = super().create(vals_list)
        for agent in ai_agents:
            if not agent.image_128:
                agent.image_128 = base64.b64encode(image_placeholder)
        return ai_agents

    # def open_agent_chat(self):
    #     self.ensure_one()
    #     return {
    #         'type': 'ir.actions.act_window',
    #         'res_model': self._name,
    #         'res_id': self.id,
    #         'view_mode': 'form',
    #         'target': 'current',
    #         'flags': {'form': {'action_buttons': True, 'options': {'mode': 'edit'}}},
    #     }

    def open_agent_chat(self):
        self.ensure_one()
        channel = self._get_or_create_ai_chat()
        return {
            'type': 'ir.actions.client',
            'tag': 'agent_chat_action',
            'params': {
                'channelId': channel.id,
            },
        }

    def _get_or_create_ai_chat(self, channel_name=None):
        channel = self._get_ai_chat_channel()
        if not channel:
            channel = self._create_ai_chat_channel(channel_name)
        return channel

    def _get_ai_chat_channel(self):
        channels = self.env['discuss.channel'].search([
            ('is_member', '=', True),
            ('channel_type', '=', 'ai_chat'),
        ])
        return channels.filtered(lambda channel: channel.sudo().ai_agent_id == self)[:1]

    def _create_ai_chat_channel(self, channel_name=None):
        guest = self.env["mail.guest"]._get_guest_from_context()
        with mute_logger("odoo.sql_db"):
            self.env.cr.execute(SQL(
                "SELECT pg_advisory_xact_lock(%s, %s);",
                guest.id if self.env.user._is_public() else self.env.user.partner_id.id,
                self.id
            ))

        channel = self.env['discuss.channel'].sudo().create({
            "ai_agent_id": self.id,
            "channel_member_ids": [
                Command.create({"guest_id": guest.id} if self.env.user._is_public() else {
                    "partner_id": self.env.user.partner_id.id}),
                Command.create({"partner_id": self.partner_id.id}),
            ],
            "channel_type": "ai_chat",
            # sudo() => visitor can set the name of the channel
            "name": channel_name if channel_name else self.partner_id.sudo().name,
        })
        return channel

    def _retrieve_chat_history(self, discuss_channel, no_messages=20):
        chat_history = [
            {
                'content': message.body,
                # sudo() => public users can access author_id (res.partner) to check whether it is an ai agent.
                'role': 'assistant' if message.sudo().author_id.agent_ids else 'user',
            }
            for message in discuss_channel.message_ids[1: no_messages + 1]
        ]

        chat_history.reverse()
        return chat_history

    @ormcache('self.env.uid', 'self.env.company.id')
    def _get_available_menus(self):
        """Get all menus accessible to the current user as CSV data."""
        all_menus = self.env["ir.ui.menu"].load_web_menus(False)
        root_menu_ids = set(all_menus["root"]["children"])

        # Collect all non-root action menus
        action_menus = []
        for menu_id, web_menu in all_menus.items():
            if menu_id == "root" or menu_id in root_menu_ids:
                continue

            # Only process menus with valid actions
            if web_menu["actionModel"] == "ir.actions.act_window":
                menu = self.env["ir.ui.menu"].browse(web_menu["id"])
                app_menu = self.env["ir.ui.menu"].browse(web_menu["appID"])

                if not menu.exists():
                    continue

                action = self.env["ir.actions.act_window"].browse(web_menu["actionID"])
                if action.exists() and action.res_model:
                    action_menus.append({
                        "menu": menu,
                        "web_menu": web_menu,
                        "action": action,
                        "app_menu": app_menu,
                    })

        # Menus are already ordered by sequence from load_web_menus(), but we still need to sort
        # by complete_name within each app to maintain proper hierarchy display
        action_menus.sort(key=lambda m: (m["app_menu"].sequence, m["menu"].complete_name))

        csv_result = "id|action_id|app|complete_name|model|model_description|available_view_types|default_view_type\n"

        for menu_data in action_menus:
            menu = menu_data["menu"]
            action = menu_data["action"]

            model_description = self.env[action.res_model]._description
            available_view_types = [view[1] for view in action.views] if action.views else []
            default_view_type = available_view_types[0] if available_view_types else "null"
            if action.view_id:
                default_view_type = action.view_id.type

            csv_result += (
                f"{menu.id}|"
                f"{action.id}|"
                f"{menu_data['app_menu'].name}|"
                f"{menu.complete_name}|"
                f"{action.res_model}|"
                f"{model_description}|"
                f"{','.join(available_view_types)}|"
                f"{default_view_type}\n"
            )

        return dedent(f"""
            ## Available Menus
            Lists all menus accessible to the current user with their associated models and views.
            Essential for finding the right menu to open based on user queries.

            Format: CSV with pipe (|) delimiter
            ```
            id|action_id|app|complete_name|model|model_description|available_view_types|default_view_type
            161|986|Accounting|Accounting/Customers/Invoices|account.move|Journal Entry|list,kanban,form,activity|list
            456|1053|Reporting|Reporting/Sales|sale.report|Sales Analysis|graph,pivot,list,form|graph
            ```

            Fields:
            - `id`: Menu identifier (use this for opening menus)
            - `action_id`: Action identifier (use this for referencing actions)
            - `app`: Root application name (e.g., Sales, Accounting, Reporting)
            - `complete_name`: Full menu path with / separators
            - `model`: Technical model name (e.g., 'sale.order', 'product.product')
            - `model_description`: Human-readable model name
            - `available_view_types`: Comma-separated supported views
            - `default_view_type`: View shown when menu opens

            ⚠️ IMPORTANT: This list does NOT include context, domain, or search_view details.
            You MUST call get_menu_details tool to retrieve this information before opening any menu.

            💡 Workflow:
            1. Use this list to find relevant menus based on model and available views
            2. Call get_menu_details tool with menu IDs to get context, domain, and search_view
            3. Parse the returned details to understand available filters and groupbys
            4. Call the appropriate open_menu_* tool with the parsed information

            💡 Tip: Prioritize "Reporting" app menus for analytical queries requiring pivot/graph views.

            {csv_result.strip()}

            Note: Use the menu id from this list when calling open_menu_* tools.
        """).strip()

    def _build_extra_system_context(self, discuss_channel):
        """Build extra system context based on the agent's configuration."""
        self.ensure_one()
        extra_context = []
        topic_xml_ids = self.topic_ids.get_external_id().values()
        if any(topic in ["ai_chatbot_enhance.ai_topic_natural_language_query", "ai_chatbot_enhance.ai_topic_information_retrieval_query"] for topic in topic_xml_ids):
            extra_context.append(self._get_available_models())
        if self.get_external_id()[self.id] == "ai_chatbot_enhance.ai_agent_natural_language_search":
            extra_context.append(self._get_available_menus())
            extra_context.append(self._get_date_calculation_reference())
        elif env_context := discuss_channel.ai_env_context:
            extra_context += env_context

        return "\n".join(extra_context) if extra_context else ""

    @ormcache('self.env.uid', 'self.env.company.id')
    def _get_available_models(self) -> str:
        """Get all models accessible to the current user as CSV data, excluding transient and abstract models."""
        # Get models the user has read access to
        allowed_models = self.env["ir.model.access"]._get_allowed_models(mode="read")

        # Get ir.model records for allowed models, excluding abstract and transient
        search_domain = [
            ('model', 'in', list(allowed_models)),
            ('transient', '=', False),
            ('_abstract', '=', False),
        ]
        model_records = self.env["ir.model"].sudo().search(search_domain, order="model")

        # Get app ordering from web menus
        all_menus = self.env["ir.ui.menu"].load_web_menus(False)
        root_menu_ids = all_menus["root"]["children"]  # This is ordered by sequence

        # Create app name to sequence mapping
        app_sequence = {}
        for idx, menu_id in enumerate(root_menu_ids):
            if menu_id in all_menus:
                app_menu = self.env["ir.ui.menu"].browse(all_menus[menu_id]["id"])
                if app_menu.exists():
                    # Get the technical name (usually matches module name)
                    app_name = all_menus[menu_id].get("xmlid", "").split(".")[0]
                    if app_name:
                        app_sequence[app_name] = idx

        # Group models by their main module/app
        models_by_app = defaultdict(list)
        for model_rec in model_records:
            # Skip models without a proper registry entry
            if model_rec.model not in self.env:
                continue

            model_obj = self.env[model_rec.model]
            # Skip models that are actually abstract despite the flag
            if model_obj._abstract or not model_obj._auto:
                continue

            # Determine the app/module (first module in the list)
            modules = model_rec.modules.split(", ") if model_rec.modules else []
            app = modules[0] if modules else "base"

            models_by_app[app].append(
                {
                    "model": model_rec.model,
                    "description": model_rec.name or model_obj._description,
                }
            )

        # Build CSV result
        csv_result = "model|description|module\n"

        # Sort apps by their menu sequence, with unknown apps at the end
        sorted_apps = sorted(
            models_by_app.keys(), key=lambda x: (app_sequence.get(x, 999), x)
        )

        for app in sorted_apps:
            for model_info in sorted(models_by_app[app], key=lambda x: x["model"]):
                csv_result += (
                    f"{model_info['model']}|{model_info['description']}|{app}\n"
                )

        return dedent(f"""
            ## Available Models
            Lists all models accessible to the current user.
            Helps identify which models to inspect when building complex queries.

            Format: CSV with pipe (|) delimiter
            ```
            model|description|module
            sale.order|Sales Order|sale
            project.project|Project|project
            project.task|Task|project
            res.partner|Contact|base
            ```

            Fields:
            - `model`: Technical model name (e.g., 'sale.order', 'res.partner')
            - `description`: Human-readable model name
            - `module`: Primary module/app where model is defined

            💡 Tip: When queries involve multiple entities, immediately call get_fields tool in PARALLEL for all relevant models to discover relationships efficiently.

            {csv_result.strip()}
        """).strip()

    def _get_date_calculation_reference(self):
        """Generate dynamic date calculation reference based on today's date."""
        today = fields.Date.context_today(self)
        today_dt = datetime.strptime(str(today), "%Y-%m-%d")

        # Calculate various date references
        yesterday = today_dt - timedelta(days=1)
        tomorrow = today_dt + timedelta(days=1)
        last_week_start = today_dt - timedelta(days=7)

        # This week (Monday to Sunday)
        days_since_monday = today_dt.weekday()
        this_week_start = today_dt - timedelta(days=days_since_monday)
        days_until_sunday = 6 - days_since_monday
        this_week_end = today_dt + timedelta(days=days_until_sunday)

        # This month (first to last day)
        this_month_start = today_dt.replace(day=1)
        # Get last day of current month
        if today_dt.month == 12:
            next_month_start = today_dt.replace(year=today_dt.year + 1, month=1, day=1)
        else:
            next_month_start = today_dt.replace(month=today_dt.month + 1, day=1)
        this_month_end = next_month_start - timedelta(days=1)

        # Last month
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        # Last 30 days
        thirty_days_ago = today_dt - timedelta(days=30)

        # This year (January 1 to December 31)
        this_year_start = today_dt.replace(month=1, day=1)
        this_year_end = today_dt.replace(month=12, day=31)

        # Last year
        last_year_start = this_year_start.replace(year=today_dt.year - 1)
        last_year_end = this_year_start - timedelta(days=1)

        # Current quarter (full quarter)
        quarter = (today_dt.month - 1) // 3 + 1
        quarter_starts = {
            1: today_dt.replace(month=1, day=1),
            2: today_dt.replace(month=4, day=1),
            3: today_dt.replace(month=7, day=1),
            4: today_dt.replace(month=10, day=1),
        }
        current_quarter_start = quarter_starts[quarter]

        # Calculate end of current quarter
        quarter_ends = {
            1: today_dt.replace(month=3, day=31),
            2: today_dt.replace(month=6, day=30),
            3: today_dt.replace(month=9, day=30),
            4: today_dt.replace(month=12, day=31),
        }
        current_quarter_end = quarter_ends[quarter]

        # Last quarter
        last_quarter = quarter - 1 if quarter > 1 else 4
        last_quarter_year = today_dt.year if quarter > 1 else today_dt.year - 1
        quarter_starts_months = {
            1: 1,   # January
            2: 4,   # April
            3: 7,   # July
            4: 10,  # October
        }
        quarter_ends = {
            1: (3, 31),   # March 31
            2: (6, 30),   # June 30
            3: (9, 30),   # September 30
            4: (12, 31),  # December 31
        }
        start_month = quarter_starts_months[last_quarter]
        last_quarter_start = datetime(last_quarter_year, start_month, 1)
        end_month, end_day = quarter_ends[last_quarter]
        last_quarter_end = datetime(last_quarter_year, end_month, end_day)

        return dedent(f"""
            ## Date Calculation Quick Reference
            Given today is {today}:
            - "yesterday" = {yesterday.strftime('%Y-%m-%d')}
            - "tomorrow" = {tomorrow.strftime('%Y-%m-%d')}
            - "last week" = {last_week_start.strftime('%Y-%m-%d')} to {today}
            - "this week" = {this_week_start.strftime('%Y-%m-%d')} to {this_week_end.strftime('%Y-%m-%d')}
            - "last month" = {last_month_start.strftime('%Y-%m-%d')} to {last_month_end.strftime('%Y-%m-%d')}
            - "this month" = {this_month_start.strftime('%Y-%m-%d')} to {this_month_end.strftime('%Y-%m-%d')}
            - "last 30 days" = {thirty_days_ago.strftime('%Y-%m-%d')} to {today}
            - "this year" = {this_year_start.strftime('%Y-%m-%d')} to {this_year_end.strftime('%Y-%m-%d')}
            - "last year" = {last_year_start.strftime('%Y-%m-%d')} to {last_year_end.strftime('%Y-%m-%d')}
            - "this quarter (Q{quarter})" = {current_quarter_start.strftime('%Y-%m-%d')} to {current_quarter_end.strftime('%Y-%m-%d')}
            - "last quarter (Q{last_quarter})" = {last_quarter_start.strftime('%Y-%m-%d')} to {last_quarter_end.strftime('%Y-%m-%d')}

            Use these exact dates when building custom domains for date-based queries.
        """).strip()

    def _get_response_for_channel(self, mail_message, channel):
        self.ensure_one()
        # prompt = html_to_inner_content(mail_message.body)
        prompt, session_info_context = self._parse_user_message(mail_message)
        try:
            extra_system_context = self._build_extra_system_context(channel)
            response = self.with_context(discuss_channel=channel)._get_response(
                prompt=prompt,
                chat_history=[{'content': session_info_context, 'role': 'user'}] + self._retrieve_chat_history(channel),
                extra_system_context=extra_system_context,
            )
        except Exception:
            if self.env.user._is_internal():
                raise
            response = [self.env._("Oops, it looks like our AI is unreachable")]
        for message in response or []:
            self._post_ai_response(channel, message)

    def _get_provider(self):
        self.ensure_one()
        return get_provider(self.env, self.llm_model)

    def _get_response(self, prompt, chat_history=None, extra_system_context=""):
        self.ensure_one()
        _logger.debug("[AI Prompt] %s", prompt)
        system_messages = self._build_system_context(extra_system_context=extra_system_context)
        if rag_context := self._build_rag_context(prompt):
            system_messages.extend(rag_context)
        llm_response = LLMApiService(env=self.env, provider=self._get_provider()).request_llm(
            self.llm_model,
            system_messages,
            [],
            inputs=(chat_history or []) + [{'role': 'user', 'content': prompt}],
            tools=self.sudo().topic_ids.tool_ids._get_ai_tools(),
            temperature=TEMPERATURE_MAP[self.response_style],
        )
        if rag_context:
            llm_response = self._get_llm_response_with_sources(llm_response)

        return llm_response

    def _parse_user_message(self, mail_message):
        self.ensure_one()
        session_info_context = ""
        if self.is_ask_ai_agent:
            context_lines = []
            context_lines.append("<session_info_context>")
            context_lines.append(
                f'  <user id="{self.env.user.id}" name="{self.env.user.display_name}" model="res.users"/>'
            )
            context_lines.append(
                f'  <partner id="{self.env.user.partner_id.id}" name="{self.env.user.partner_id.name}" model="res.partner"/>'
            )
            context_lines.append(
                f'  <company id="{self.env.company.id}" name="{self.env.company.name}" model="res.company"/>'
            )

            user_context = dict(self.env['res.users'].context_get())
            if user_context.get("tz"):
                context_lines.append(
                    f'    <timezone value="{user_context["tz"]}"/>'
                )

            # Current view as a single element if present
            if current_view_info := self.env.context.get("current_view_info"):
                action_id = current_view_info.get("action_id")
                action = self.env['ir.actions.actions'].browse(action_id)
                current_action = None
                current_action_name = None
                if action.type == 'ir.actions.act_window':
                    current_action = self.env['ir.actions.act_window'].browse(action_id)
                elif action.type == 'ir.actions.server':
                    current_action = self.env['ir.actions.server'].browse(action_id)
                elif action.type == 'ir.actions.client':
                    current_action = self.env['ir.actions.client'].browse(action_id)
                if current_action:
                    current_action_name = current_action.name
                    if action.type == 'ir.actions.act_window':
                        search_view = self.env[current_action.res_model].get_view(current_action.search_view_id.id, 'search')
                        search_view_xml = clean_search_view_xml(search_view['arch']) if search_view else ""
                        if search_view_xml:
                            context_lines.append(f"  {search_view_xml}")

                context_lines.append(
                    f'  <current_view id="{current_view_info.get("view_id")}" '
                    f'model="{current_view_info.get("model")}" '
                    f'type="{current_view_info.get("view_type")}" '
                    f'action_id="{action_id}" '
                    f'action="{current_action_name}" '
                    f'available_view_types="{current_view_info.get("available_view_types")}"/>'
                )

                facets = current_view_info.get("facets", [])
                if facets:
                    context_lines.append(f'  <active_search_facets>\n    {self._facets_to_xml(facets)}\n  </active_search_facets>')

            context_lines.append("</session_info_context>")
            session_info_context = "\n".join(context_lines) + "\n" + dedent("""
                The above provides information about the current user and where in the app he is at.
                <session_info_context> contains important information about the the user. partner element is the linked res.partner record to the user.
                It may also contain info about the <current_view> that I'm in in the UI.
                <active_search_facets>, if exists, contains the currently active facets in the shown search bar in the UI.
                <search> is the specification of the search bar in the UI. It contains the blueprint of the things that can be done to it by the user. Information from it can be useful when calling terminating tool calls.
                Knowing <current_view>, <active_search_facets>, and/or <search> provides you a rough idea of where the user is and what he's looking at.
            """.strip())
        return html_to_inner_content(mail_message.body), session_info_context

    def _build_system_context(self, extra_system_context: str = ""):
        self.ensure_one()
        system_content = self.system_prompt or "You are a RAG assistant."
        system_content += f"\n\nToday's date to be used: {fields.Datetime.now()} (UTC)"
        if not self.env.user._is_public():
            partner_vals, _ = self.env.user.partner_id._ai_read(['name', 'function', 'email', 'phone'], None)
            system_content += f"\n\nUser info: {partner_vals}"
        system_content += f"\nAll record data timestamps are in UTC. In responses, convert them to {self.env.user.tz or 'UTC'}"

        if self.topic_ids:
            system_content += PREPROMPTS['tools']

        messages = [system_content]

        if self.topic_ids:
            topic_instructions = "\n\n".join(
                [topic.instructions for topic in self.topic_ids if topic.instructions])
            if topic_instructions:
                messages.append(f"Additional topic instructions:\n{topic_instructions}.")

        if self.restrict_to_sources:
            messages.append(PREPROMPTS['restrict_to_sources'])

        if isinstance(extra_system_context, str):
            messages.append(extra_system_context)
        elif isinstance(extra_system_context, list):
            messages += extra_system_context

        return messages

    def _build_rag_context(self, prompt):
        self.ensure_one()
        messages = []
        context = ""
        if self.sources_ids:
            provider = self._get_provider()
            embedding_model = self._get_embedding_ai_model()
            response = LLMApiService(env=self.env, provider=provider).get_ai_embedding(
                input=prompt,
                dimensions=self.env['ai.embedding']._get_dimensions(),
                model=embedding_model
            )
            if not response or "data" not in response:
                raise UserError(_("Failed to get embeddings for the prompt."))

            prompt_embedding = response['data'][0]['embedding']
            similar_embeddings = self.env['ai.embedding']._get_ai_similar_chunks(
                query_embedding=prompt_embedding,
                sources=self.sources_ids,
                embedding_model=self._get_embedding_ai_model(),
                top_n=5
            )
            if similar_embeddings:
                embeddings_attachment_checksums = similar_embeddings.mapped('attachment_id.checksum')
                agent_sources = self.env['ai.agent.source'].search([
                    ('attachment_id.checksum', 'in', embeddings_attachment_checksums),
                    ('agent_id', '=', self.id),
                ])
                source_map = {source.attachment_id.checksum: source for source in agent_sources}
                for embedding in similar_embeddings:
                    checksum = embedding.attachment_id.checksum
                    agent_source = source_map[checksum]
                    context += (
                        f"(Source Chunk {agent_source.name})\n"
                        f" (attachment_id: {agent_source.attachment_id.id})\n"
                        f"{embedding.content}\n\n"
                    )

                final_context_message = f"##RAG context information:\n\n{context}"

                messages.append(final_context_message)
                messages.append(PREPROMPTS['context'])
        return messages

    def _post_ai_response(self, channel, message):
        formatted_message = message
        if markdown:
            raw_html = markdown(message, extras=['fenced-code-blocks', 'tables', 'strike'])
            formatted_message = html_sanitize(raw_html)
        else:
            formatted_message = html_sanitize(message)
        channel.sudo().message_post(
            author_id=self.partner_id.id,
            body=formatted_message,
            message_type='comment',
            # subtype_xmlid='mail.mt_comment'
        )

    @api.depends("sources_ids.status")
    def _compute_sources_fully_processed(self):
        for record in self:
            record.sources_fully_processed = not record.sources_ids.filtered(lambda s: s.status == 'processing')

    def _get_embedding_ai_model(self):
        self.ensure_one()
        provider = self._get_provider()
        supported_models = {
            'openai': 'text-embedding-3-small',
            'google': 'gemini-embedding-001',
            'qwen': 'text-embedding-v2',
        }
        if provider not in supported_models:
            # return ""
            raise UserError(_("Only OpenAI/Google/QWen are supported. Invalid provider: %s") % provider)
        return supported_models[provider]

    def _get_llm_response_with_sources(self, llm_response):
        """
        Parses inline citations (e.g., [SOURCE:210]) from each LLM message,
        replaces them with clickable sequential superscript numbers, and enriches
        the message content with a numbered list of corresponding source names
        and links.

        :param llm_response: The list of messages from the LLM
        :type llm_response: list[str]
        :return: The list of messages with the sources added
        :rtype: list[str]
        """
        llm_response_with_sources = []
        base_url = self.get_base_url()
        link_attrs = 'target="_blank" rel="noreferrer noopener"'

        for message_content in llm_response:
            unique_attachment_ids = get_attachment_ids_from_text(message_content)
            attachment_data = {}
            accessible_sources = self.env['ai.agent.source']
            if unique_attachment_ids:
                sources = self.env['ai.agent.source'].search([
                    ('attachment_id', 'in', unique_attachment_ids),
                    ('agent_id', '=', self.id),
                ])
                accessible_sources = sources.filtered(lambda s: s.user_has_access)
                for source in accessible_sources:
                    attachment_data[source.attachment_id.id] = {
                        'source_name': source.name,
                        'url': source.url or f"{base_url}/web/content/{source.attachment_id.id}",
                    }

            new_content = apply_numeric_citations(message_content, attachment_data, link_attrs=link_attrs)
            llm_response_with_sources.append(new_content)

        return llm_response_with_sources

    def action_refresh_sources(self):
        """
        Refresh the sources to show the new status if any was changed by the cron.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'soft_reload',
        }

