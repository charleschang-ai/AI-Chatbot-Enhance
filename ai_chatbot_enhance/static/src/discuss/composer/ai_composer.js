/** @odoo-module **/

import {Composer} from "@mail/core/common/composer";
import {patch} from "@web/core/utils/patch";
import {useService} from "@web/core/utils/hooks";
import {useState, onWillStart, onWillUpdateProps} from "@odoo/owl";

patch(Composer.prototype, {
    setup() {
        super.setup();

        this.orm = useService("orm");

        this.agentState = useState({
            currentAgent: null,
        });

        this.lastFetchedAgentId = null;

        onWillStart(async () => {
            await this.fetchAgentViaOrm();
        });

        onWillUpdateProps(async (nextProps) => {
            await this.fetchAgentViaOrm(nextProps);
        });
    },

    async fetchAgentViaOrm(nextProps) {
        const composer = nextProps?.composer || this.props.composer;
        const thread = composer?.thread;

        if (thread && thread.type === 'ai_chat' && thread.ai_agent_id) {   // ← channel_type 改 type
            let currentId = 0;
            if (Array.isArray(thread.ai_agent_id)) {
                currentId = thread.ai_agent_id[0];
            } else if (typeof thread.ai_agent_id === 'object') {
                currentId = thread.ai_agent_id.id;
            } else {
                currentId = Number(thread.ai_agent_id);
            }

            if (currentId && currentId === this.lastFetchedAgentId) {
                return;
            }
            this.lastFetchedAgentId = currentId;

            try {
                const [agentData] = await this.orm.searchRead(
                    "ai.agent",
                    [["id", "=", currentId]],
                    ["id", "name", "llm_model"]
                );
                if (agentData) {
                    this.agentState.currentAgent = agentData;
                }
            } catch (error) {
                console.error("Composer orm search Agent failed:", error);
                this.agentState.currentAgent = null;
            }
        } else {
            this.agentState.currentAgent = null;
            this.lastFetchedAgentId = null;
        }
    },

    get currentAgentInfo() {
        return this.agentState.currentAgent;
    },

});