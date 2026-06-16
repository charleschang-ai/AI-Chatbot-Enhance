/** @odoo-module **/

import { registry } from "@web/core/registry";
import { formatDate, formatDateTime } from "@web/core/l10n/dates";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

export const aiChatLauncherService = {
    // ← 加上 "mail.thread"，拿 threadService
    dependencies: ["mail.store", "mail.thread", "action", "orm"],

    start(env, { "mail.store": mailStore, "mail.thread": threadService, action, orm }) {

        async function openFullComposer(msgType, resModel, resId, content) {
            const thread = mailStore.Thread.insert({ model: resModel, id: resId });
            if (!thread) {
                console.warn("Thread not found in mail store");
                return;
            }

            let allRecipients = [];
            if (msgType === "message") {
                allRecipients = [...thread.suggestedRecipients];
                const newPartners = allRecipients.filter((recipient) => !recipient.partner_id);
                if (newPartners.length) {
                    const recipientEmails = newPartners.map((recipient) => recipient.email);
                    const partners = await orm.call("res.partner", "find_or_create_from_emails", [recipientEmails]);
                    for (let i = 0; i < partners.length; i++) {
                        const partnerData = partners[i];
                        const email = recipientEmails[i];
                        const recipient = allRecipients.find((r) => r.email === email);
                        if (recipient) recipient.partner_id = partnerData.id;
                    }
                }
            }

            action.doAction(
                {
                    name: msgType === "message" ? _t("Send Message") : _t("Log Note"),
                    res_model: "mail.compose.message",
                    target: "new",
                    type: "ir.actions.act_window",
                    view_id: false,
                    view_mode: "form",
                    views: [[false, "form"]],
                    context: {
                        clicked_on_full_composer: true,
                        default_body: content,
                        default_model: resModel,
                        default_partner_ids: allRecipients.map((r) => r.partner_id).filter(id => id),
                        default_res_ids: [resId],
                        default_subtype_xmlid: msgType === "message" ? "mail.mt_comment" : "mail.mt_note",
                    },
                },
                { onClose: () => thread.fetchNewMessages?.() }
            );
        }

        function recordDataToContextJSON(recordData, fieldsInfo) {
            const result = {};
            for (const [fieldName, fieldValue] of Object.entries(recordData)) {
                const fieldInfo = fieldsInfo[fieldName] || {};
                if (fieldInfo.type === "binary") continue;
                if (["many2one", "many2many", "one2many"].includes(fieldInfo.type)) {
                    if (fieldValue?.records?.length > 50) continue;
                    if (fieldInfo.type === "many2one") {
                        result[fieldName] = fieldValue?.display_name || fieldValue?.name || null;
                    } else {
                        result[fieldName] = fieldValue?.records?.map(r => r.data.display_name || r.data.name) || [];
                    }
                } else if (fieldInfo.type === "date" && fieldValue) {
                    const dt = luxon.DateTime.fromISO(fieldValue);
                    result[fieldName] = dt.isValid ? formatDate(dt) : fieldValue;
                } else if (fieldInfo.type === "datetime" && fieldValue) {
                    const dt = luxon.DateTime.fromISO(fieldValue);
                    result[fieldName] = dt.isValid ? formatDateTime(dt) : fieldValue;
                } else {
                    result[fieldName] = fieldValue;
                }
            }
            return result;
        }

        return {
            async launchAIChat({
                callerComponentName,
                recordModel,
                recordId,
                channelTitle,
                aiSpecialActions,
                aiChatSourceId,
                originalRecordData = null,
                originalRecordFields = null,
                textSelection = null,
                agentId = null,
            }) {
                let frontEndRecordInfo;
                if (["html_field_record", "html_field_text_select", "chatter_ai_button"].includes(callerComponentName)) {
                    frontEndRecordInfo = recordDataToContextJSON(originalRecordData, originalRecordFields);
                }
                mailStore.aiInsertButtonTarget = aiChatSourceId;

                const result = await orm.call("discuss.channel", "create_ai_draft_channel", [
                    callerComponentName,
                    channelTitle,
                    recordModel,
                    recordId,
                    frontEndRecordInfo,
                    textSelection,
                    agentId,
                ]);
                const { ai_channel_id, prompts, model_has_thread } = result;

                const thread = await threadService.fetchChannel(Number(ai_channel_id));
                if (!thread) {
                    console.error("Failed to get AI channel thread");
                    return;
                }

                const promptButtons = prompts.map((text, idx) => ({ id: idx, text }));
                browser.localStorage.setItem(`ai.thread.prompt_buttons.${ai_channel_id}`, JSON.stringify(promptButtons));

                if (callerComponentName === "chatter_ai_button" && model_has_thread) {
                    aiSpecialActions = {
                        ...(aiSpecialActions || {}),
                        sendMessage: (content) => openFullComposer("message", recordModel, recordId, content),
                        logNote: (content) => openFullComposer("note", recordModel, recordId, content),
                    };
                }
                thread.aiSpecialActions = aiSpecialActions;
                thread.aiChatSource = aiChatSourceId;

                threadService.open(thread);
            },
        };
    },
};

registry.category("services").add("simpleAIChat", aiChatLauncherService);