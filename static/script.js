
"use strict";

/* ==================================================
   NOVARA AI — MAIN FRONTEND SCRIPT
   Includes chats, images, login, theme, search,
   pricing, PayPal and multilingual voice input.
================================================== */

const chatForm = document.getElementById("chatForm");
const messageInput = document.getElementById("messageInput");
const messages = document.getElementById("messages");
const welcome = document.getElementById("welcome");
const chatList = document.getElementById("chatList");
const newChatButton = document.getElementById("newChat");
const addChatButton = document.getElementById("addChat");
const sendButton = document.getElementById("sendButton");
const menuButton = document.getElementById("menuButton");
const themeButton = document.getElementById("themeButton");
const upgradeButton = document.getElementById("upgradeButton");
const attachButton = document.getElementById("attachButton");
const fileInput = document.getElementById("fileInput");
const emojiButton = document.getElementById("emojiButton");
const searchButton = document.getElementById("searchButton");

let chats = [];
let currentChatId = null;
let isSending = false;
let currentUser = null;
let selectedImageData = null;
let selectedImageName = "";

async function api(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...(options.body instanceof FormData
                ? {}
                : { "Content-Type": "application/json" }),
            ...(options.headers || {})
        },
        credentials: "same-origin"
    });

    if (response.status === 401) {
        window.location.href = "/login/google";
        throw new Error("Please sign in with Google.");
    }

    if (!response.ok) {
        let errorMessage = `Request failed (${response.status})`;

        try {
            const data = await response.json();
            errorMessage = data.detail || errorMessage;
        } catch {}

        throw new Error(errorMessage);
    }

    if (response.status === 204) return null;

    return response.json();
}

/* ---------------- CHAT DATA ---------------- */

function normaliseChat(data) {
    const chat = data.chat || data;

    return {
        id: String(chat.id),
        title: chat.title || "New conversation",
        messages: Array.isArray(chat.messages)
            ? chat.messages.map(message => ({
                role: message.role,
                content: message.content || ""
            }))
            : []
    };
}

function getCurrentChat() {
    return chats.find(chat => chat.id === String(currentChatId));
}

/* ---------------- PROFILE / LOGIN ---------------- */

function updateProfile(user) {
    const profile = document.querySelector(".profile");
    if (!profile) return;

    const avatar = profile.querySelector(".avatar");
    const info = profile.querySelector(".profile-info");

    if (avatar) {
        avatar.replaceChildren();

        if (user && user.picture) {
            const image = document.createElement("img");
            image.src = user.picture;
            image.alt = "Google profile picture";
            image.referrerPolicy = "no-referrer";
            image.style.width = "100%";
            image.style.height = "100%";
            image.style.objectFit = "cover";
            image.style.borderRadius = "inherit";

            image.onerror = () => {
                avatar.textContent =
                    (user.name || "U").charAt(0).toUpperCase();
            };

            avatar.appendChild(image);
        } else {
            avatar.textContent = user
                ? (user.name || "U").charAt(0).toUpperCase()
                : "N";
        }
    }

    if (info) {
        info.replaceChildren();

        const name = document.createElement("strong");
        name.textContent = user ? user.name : "Not signed in";

        const plan = document.createElement("span");
        plan.textContent = user ? "Free Plan" : "Sign in to continue";

        info.append(name, plan);
    }

    profile.title = user
        ? "Signed in as " + (user.email || user.name)
        : "Sign in with Google";

    profile.style.cursor = "pointer";
    profile.onclick = () => {
        if (currentUser) {
            if (confirm("Sign out of Novara AI?")) logout();
        } else {
            window.location.href = "/login/google";
        }
    };
}

async function logout() {
    try {
        await fetch("/logout", {
            method: "POST",
            credentials: "same-origin"
        });
    } finally {
        window.location.href = "/";
    }
}

async function checkLogin() {
    try {
        const response = await fetch("/api/auth/me", {
            credentials: "same-origin"
        });

        if (!response.ok) {
            currentUser = null;
            updateProfile(null);
            window.location.href = "/login/google";
            return false;
        }

        const data = await response.json();
        currentUser = data.user || data;
        updateProfile(currentUser);
        return true;
    } catch (error) {
        console.error("Could not check sign-in:", error);
        return false;
    }
}

/* ---------------- CHAT HISTORY ---------------- */

async function loadChats(selectLatest = false) {
    try {
        const data = await api("/api/chats");
        const chatData = Array.isArray(data) ? data : data.chats || [];

        chats = chatData.map(normaliseChat);

        if (
            selectLatest ||
            !chats.some(chat => chat.id === String(currentChatId))
        ) {
            currentChatId = chats.length ? chats[0].id : null;
        }

        renderChatList();

        if (currentChatId) {
            await loadChat(currentChatId);
        } else {
            renderMessages();
        }
    } catch (error) {
        console.error("Could not load saved chats:", error);
    }
}

async function loadChat(id) {
    const requestedId = String(id);

    try {
        const data = await api(
            `/api/chats/${encodeURIComponent(requestedId)}`
        );

        const chat = normaliseChat(data);

        if (!chat.id || chat.id === "undefined") {
            throw new Error("The server returned an invalid chat.");
        }

        const index = chats.findIndex(item => item.id === chat.id);

        if (index >= 0) {
            chats[index] = chat;
        } else {
            chats.unshift(chat);
        }

        currentChatId = chat.id;

        renderChatList();
        renderMessages();
    } catch (error) {
        console.error("Could not load conversation:", error);
        alert("Couldn't open this conversation: " + error.message);
    }
}

async function createChat() {
    if (isSending) return;

    try {
        const data = await api("/api/chats", {
            method: "POST",
            body: JSON.stringify({})
        });

        const chat = normaliseChat(data);

        chats = chats.filter(item => item.id !== chat.id);
        chats.unshift(chat);
        currentChatId = chat.id;

        renderChatList();
        renderMessages();
        messageInput?.focus();
    } catch (error) {
        console.error("Could not create chat:", error);
        alert("Couldn't create a new conversation: " + error.message);
    }
}

function renderChatList() {
    if (!chatList) return;

    chatList.replaceChildren();

    chats.forEach(chat => {
        if (!chat.messages.length && chat.title === "New conversation") {
            return;
        }

        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-history-item";

        if (chat.id === String(currentChatId)) {
            button.classList.add("active");
        }

        const icon = document.createElement("span");
        icon.className = "chat-icon";
        icon.textContent = "▢";

        const title = document.createElement("span");
        title.className = "chat-title";
        title.textContent = chat.title;

        button.append(icon, title);
        button.addEventListener("click", () => {
            currentChatId = chat.id;
            renderChatList();
            loadChat(chat.id);
        });

        chatList.appendChild(button);
    });
}

function renderMessages() {
    if (!messages || !welcome) return;

    messages.replaceChildren();

    const chat = getCurrentChat();
    const hasMessages = Boolean(chat && chat.messages.length > 0);

    welcome.hidden = hasMessages;
    messages.hidden = !hasMessages;

    if (!hasMessages) return;

    chat.messages.forEach(message => {
        const row = document.createElement("div");
        row.className = `message ${message.role}`;

        if (message.role === "assistant") {
            const avatar = document.createElement("div");
            avatar.className = "message-avatar";
            avatar.textContent = "✦";
            row.appendChild(avatar);
        }

        const bubble = document.createElement("div");
        bubble.className = "message-body";
        bubble.textContent = message.content;
        row.appendChild(bubble);
        messages.appendChild(row);
    });

    const conversation = document.querySelector(".conversation");

    if (conversation) {
        conversation.scrollTop = conversation.scrollHeight;
    }
}

function addMessageLocally(role, content) {
    const chat = getCurrentChat();
    if (!chat) return;

    chat.messages.push({ role, content });

    if (role === "user" && chat.title === "New conversation") {
        chat.title = content.length > 35
            ? content.slice(0, 35) + "..."
            : content;
    }

    renderChatList();
    renderMessages();
}

async function refreshChatList() {
    try {
        const data = await api("/api/chats");
        const list = Array.isArray(data) ? data : data.chats || [];

        const refreshed = list.map(item => {
            const id = String(item.id);
            const existing = chats.find(chat => chat.id === id);

            return {
                id,
                title: item.title || "New conversation",
                messages: existing ? existing.messages : []
            };
        });

        chats = refreshed;

        renderChatList();
        renderMessages();
    } catch (error) {
        console.error("Could not refresh chat list:", error);
    }
}

/* ---------------- SEND MESSAGES ---------------- */

async function sendMessage(text) {
    const content = (text || messageInput.value).trim();

    if ((!content && !selectedImageData) || isSending) return;

    const imageToSend = selectedImageData;
    const imageNameToSend = selectedImageName;

    const messageToSend = content ||
        `What is in this image? (${imageNameToSend})`;

    if (!currentChatId || !getCurrentChat()) {
        await createChat();
    }

    if (!currentChatId || !getCurrentChat()) {
        alert("Couldn't create a conversation. Please try again.");
        return;
    }

    const chatId = String(currentChatId);

    messageInput.value = "";
    messageInput.style.height = "auto";

    isSending = true;

    if (sendButton) {
        sendButton.disabled = true;
        sendButton.textContent = "…";
    }

    addMessageLocally(
        "user",
        imageToSend
            ? `${messageToSend}\n[Image: ${imageNameToSend}]`
            : messageToSend
    );

    const typing = document.createElement("div");
    typing.className = "message typing";
    typing.id = "typingIndicator";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "✦";

    const bubble = document.createElement("div");
    bubble.className = "message-body";
    bubble.textContent = "Novara AI is thinking…";

    typing.append(avatar, bubble);
    messages.appendChild(typing);
    messages.hidden = false;
    welcome.hidden = true;

    try {
        const data = await api("/chat", {
            method: "POST",
            body: JSON.stringify({
                message: messageToSend,
                chat_id: chatId,
                image_data: imageToSend
            })
        });

        if (data.chat_id != null) {
            currentChatId = String(data.chat_id);
        }

        addMessageLocally(
            "assistant",
            data.reply || "I couldn't generate a response."
        );

        await loadChat(currentChatId);
        await refreshChatList();
    } catch (error) {
        console.error("Message request failed:", error);

        if (!error.message.includes("sign in")) {
            addMessageLocally(
                "assistant",
                "Sorry, I couldn't get a response. " + error.message
            );
        }
    } finally {
        document.getElementById("typingIndicator")?.remove();

        isSending = false;

        if (sendButton) {
            sendButton.disabled = false;
            sendButton.textContent = "➤";
        }

        if (imageToSend) {
            selectedImageData = null;
            selectedImageName = "";

            if (attachButton) {
                attachButton.textContent = "♧";
                attachButton.title = "Attach image";
            }

            messageInput.placeholder = "Type your message here...";
        }

        messageInput.focus();
    }
}

/* ---------------- CHAT FORM ---------------- */

chatForm?.addEventListener("submit", event => {
    event.preventDefault();
    sendMessage();
});

messageInput?.addEventListener("input", () => {
    messageInput.style.height = "auto";
    messageInput.style.height =
        Math.min(messageInput.scrollHeight, 180) + "px";
});

messageInput?.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chatForm.requestSubmit();
    }
});

newChatButton?.addEventListener("click", createChat);
addChatButton?.addEventListener("click", createChat);

document.querySelectorAll(".suggestion-card").forEach(button => {
    button.addEventListener("click", () => {
        sendMessage(button.dataset.prompt);
    });
});

/* ---------------- APP CONTROLS ---------------- */

menuButton?.addEventListener("click", () => {
    document.body.classList.toggle("sidebar-hidden");
});

themeButton?.addEventListener("click", () => {
    document.body.classList.toggle("light-mode");
});

attachButton?.addEventListener("click", () => {
    fileInput?.click();
});

fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;

    const allowedTypes = [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif"
    ];

    if (!allowedTypes.includes(file.type)) {
        alert("Please choose a PNG, JPG, WEBP, or GIF image.");
        fileInput.value = "";
        return;
    }

    if (file.size > 5 * 1024 * 1024) {
        alert("Please choose an image smaller than 5 MB.");
        fileInput.value = "";
        return;
    }

    const reader = new FileReader();

    reader.onload = () => {
        selectedImageData = reader.result;
        selectedImageName = file.name;

        if (attachButton) {
            attachButton.title = "Selected: " + file.name;
            attachButton.textContent = "✓";
        }

        messageInput.placeholder = "Ask a question about " + file.name;
        messageInput.focus();
    };

    reader.onerror = () => {
        alert("Could not read that image. Please try another.");
        selectedImageData = null;
        selectedImageName = "";
    };

    reader.readAsDataURL(file);
    fileInput.value = "";
});

/* ---------------- EMOJI BUTTON ---------------- */

emojiButton?.addEventListener("click", () => {
    if (!messageInput) return;
    messageInput.value += " 😊";
    messageInput.focus();
});

/* ---------------- SEARCH CHATS ---------------- */

searchButton?.addEventListener("click", () => {
    const query = prompt("What would you like to find in your chats?");
    if (!query) return;

    const term = query.toLowerCase();

    const match = chats.find(chat =>
        chat.title.toLowerCase().includes(term) ||
        chat.messages.some(message =>
            message.content.toLowerCase().includes(term)
        )
    );

    if (match) {
        currentChatId = match.id;
        loadChat(match.id);
    } else {
        alert("No matching chat found in the conversations currently loaded.");
    }
});

/* ---------------- NAVIGATION ---------------- */

document.querySelectorAll(".nav-item").forEach(button => {
    button.addEventListener("click", () => {
        document.querySelectorAll(".nav-item").forEach(item => {
            item.classList.remove("active");
        });

        button.classList.add("active");

        const page = button.dataset.page;

        if (page === "home") {
            renderMessages();
        } else if (page === "history") {
            refreshChatList();
        } else if (page === "explore") {
            alert("Choose one of the suggestion cards to explore an idea.");
        } else if (page === "settings") {
            alert("Use the theme icon in the top bar to switch the theme.");
        }
    });
});

document.addEventListener("keydown", event => {
    if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "n"
    ) {
        event.preventDefault();
        createChat();
    }

    if (event.key === "Escape" && window.innerWidth <= 760) {
        document.body.classList.add("sidebar-hidden");
    }
});

/* ---------------- INITIALISE ---------------- */

async function initialiseNovara() {
    const signedIn = await checkLogin();

    if (signedIn) {
        await loadChats(true);
    }
}

initialiseNovara();

/* ---------------- PRICING / PAYPAL ---------------- */

const pricingModal = document.getElementById("pricingModal");
const closePricing = document.getElementById("closePricing");
const freePlanButton = document.getElementById("freePlanButton");
const subscribeButton = document.getElementById("subscribeButton");

function openPricingModal() {
    if (!pricingModal) return;
    pricingModal.classList.add("open");
    pricingModal.setAttribute("aria-hidden", "false");
}

function closePricingModal() {
    if (!pricingModal) return;
    pricingModal.classList.remove("open");
    pricingModal.setAttribute("aria-hidden", "true");
}

upgradeButton?.addEventListener("click", openPricingModal);
closePricing?.addEventListener("click", closePricingModal);
freePlanButton?.addEventListener("click", closePricingModal);

pricingModal?.addEventListener("click", event => {
    if (event.target === pricingModal) closePricingModal();
});

document.addEventListener("keydown", event => {
    if (event.key === "Escape") closePricingModal();
});

subscribeButton?.addEventListener("click", async () => {
    try {
        const response = await fetch("/api/paypal/create-subscription", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({})
        });

        const data = await response.json();

        if (!response.ok) {
            alert(
                data.detail ||
                "Could not start the subscription. Please sign in and try again."
            );
            return;
        }

        if (data.approval_url) {
            window.location.href = data.approval_url;
        } else {
            alert("PayPal did not return an approval link. Please try again.");
        }
    } catch (error) {
        console.error("Subscription error:", error);
        alert("Could not connect to PayPal. Please try again.");
    }
});

/* ---------------- VOICE INPUT ---------------- */

(() => {
    const voiceButton = document.getElementById("voiceButton");
    const languageSelect = document.getElementById("voiceLanguage");
    const input = document.getElementById("messageInput");

    if (!voiceButton || !input) {
        console.error(
            "Voice input: missing #voiceButton or #messageInput. Check index.html."
        );
        return;
    }

    const SpeechRecognition =
        window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        voiceButton.title = "Voice recognition is not supported here";

        voiceButton.addEventListener("click", () => {
            alert(
                "Voice input is not supported by this browser. " +
                "Please try the latest Microsoft Edge or Google Chrome."
            );
        });

        return;
    }

    let recognition = null;
    let listening = false;
    let textBeforeRecording = "";
    let finalTranscript = "";

    function setListeningUI(active) {
        listening = active;
        voiceButton.classList.toggle("listening", active);
        voiceButton.textContent = active ? "⏹" : "🎙️";
        voiceButton.title = active
            ? "Stop voice input"
            : "Start voice input";
        voiceButton.setAttribute(
            "aria-label",
            active ? "Stop voice input" : "Start voice input"
        );
    }

    voiceButton.addEventListener("click", () => {
        if (listening) {
            try {
                recognition?.stop();
            } catch (error) {
                console.error("Could not stop voice input:", error);
                setListeningUI(false);
            }
            return;
        }

        const newRecognition = new SpeechRecognition();
        recognition = newRecognition;

        newRecognition.lang = languageSelect?.value || "en-GB";
        newRecognition.continuous = true;
        newRecognition.interimResults = true;
        newRecognition.maxAlternatives = 1;

        textBeforeRecording = input.value.trimEnd();
        finalTranscript = "";

        newRecognition.onstart = () => {
            setListeningUI(true);
        };

        newRecognition.onresult = event => {
            let interimTranscript = "";

            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                const words = result[0].transcript;

                if (result.isFinal) {
                    finalTranscript += words + " ";
                } else {
                    interimTranscript += words;
                }
            }

            const spokenText =
                (finalTranscript + interimTranscript).trim();

            input.value = [
                textBeforeRecording,
                spokenText
            ].filter(Boolean).join(textBeforeRecording ? " " : "");

            input.dispatchEvent(new Event("input", { bubbles: true }));
        };

        newRecognition.onerror = event => {
            console.error("Voice recognition error:", event.error);

            const errors = {
                "not-allowed":
                    "Microphone access was blocked. Allow microphone access for this website in your browser settings.",
                "service-not-allowed":
                    "Your browser blocked the speech recognition service.",
                "audio-capture":
                    "No microphone was detected. Check your microphone connection and settings.",
                "language-not-supported":
                    "The selected language is not supported by this browser.",
                "network":
                    "Voice recognition needs an internet connection. Check your connection and try again."
            };

            if (errors[event.error]) {
                alert(errors[event.error]);
            } else if (
                event.error !== "no-speech" &&
                event.error !== "aborted"
            ) {
                alert("Voice input error: " + event.error);
            }
        };

        newRecognition.onend = () => {
            if (recognition === newRecognition) {
                recognition = null;
                setListeningUI(false);
                input.focus();
            }
        };

        try {
            newRecognition.start();
        } catch (error) {
            console.error("Could not start voice input:", error);
            recognition = null;
            setListeningUI(false);
            alert(
                "Couldn't start voice input. Refresh the page and try again."
            );
        }
    });

    languageSelect?.addEventListener("change", () => {
        if (listening) {
            alert("Stop voice input before changing the language.");
        }
    });
})();
