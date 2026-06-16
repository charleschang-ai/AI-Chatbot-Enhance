/** @odoo-module **/

import {Thread} from "@mail/core/common/thread_model";
import {patch} from "@web/core/utils/patch";
import {browser} from "@web/core/browser/browser";
import {url} from "@web/core/utils/urls";
import {assignDefined} from "@mail/utils/common/misc";

const AI_PROMPT_BUTTONS = "ai.thread.prompt_buttons.";

patch(Thread.prototype, {
    setup() {
        super.setup();
        // this.ai_prompt_buttons = Record.many("ai.prompt.button", {
        //     inverse: "thread_id",
        //     compute() {
        //         return JSON.parse(browser.localStorage.getItem(AI_PROMPT_BUTTONS.concat(this.id)));
        //     },
        // });
        // const stored = localStorage.getItem(AI_PROMPT_BUTTONS.concat(this.props.record.id));
        // this.ai_prompt_buttons = stored ? JSON.parse(stored) : [];
    },
    async closeChatWindow(options = {}) {
        await super.closeChatWindow(options);
        browser.localStorage.removeItem(AI_PROMPT_BUTTONS.concat(this.id));
    },

    get avatarUrl() {
        if (this.channel_type === "ai_chat" && this.ai_agent_id) {
            return `/web/image/ai.agent/${this.ai_agent_id}/avatar_128`;
        }
        return super.avatarUrl;
    },

    // get avatarUrl() {
    //     if (this.channel_type === "ai_chat" && this.correspondent) {
    //         return this.correspondent.avatarUrl;
    //     }
    //     return super.avatarUrl;
    // },

    get imgUrl() {
        if (this.type === "ai_chat") {
            return url(
                `/web/image/discuss.channel/${this.id}/avatar_128`,
                assignDefined({}, {unique: this.avatarCacheKey})
            );
        }
        return super.imgUrl;
    },

    computeCorrespondent() {
        const correspondent = super.computeCorrespondent();
        if (
            ["ai_composer", "ai_chat"].includes(this.channel_type) &&
            correspondent?.persona?.eq(this.store.self)
        ) {
            return undefined;
        }
        return correspondent;
    },
    _computeDiscussAppCategory() {
        if (this.parent_channel_id) {
            return;
        }
        if (["group", "chat", "ai_chat"].includes(this.channel_type)) {
            return this.store.discuss.chats;
        }
        if (this.channel_type === "channel") {
            return this.store.discuss.channels;
        }
    },
});
