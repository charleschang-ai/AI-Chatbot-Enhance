import {Component, useState, onWillStart} from "@odoo/owl";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {Dropdown} from "@web/core/dropdown/dropdown";
import {DropdownItem} from "@web/core/dropdown/dropdown_item";

export default class SystrayAction extends Component {
    static props = {};
    static template = "ai.SystrayAction";
    static components = {Dropdown, DropdownItem};

    setup() {
        super.setup();
        this.actionService = useService("action");
        this.aiChatLauncher = useService("simpleAIChat");
        this.orm = useService("orm");

        this.state = useState({
            agents: []
        });

        onWillStart(async () => {
            await this.loadAgents();
        });
    }

    async onDropdownOpened() {
        await this.loadAgents();
    }

    async loadAgents() {
        try {
            const agents = await this.orm.searchRead(
                "ai.agent",
                [],
                ["id", "name", "llm_model", "image_128"]
            );

            this.state.agents = agents;

        } catch (error) {
            console.error("Pull AI Agent list failed:", error);
        }
    }

    async onClickLaunchAIChat(agent) {
        await this.aiChatLauncher.launchAIChat({
            callerComponentName: "systray_ai_button",
            agentId: agent.id,
            agentName: agent.name
        });
    }
}

registry.category("systray").add("ai.systray_action", {Component: SystrayAction}, {sequence: 30});