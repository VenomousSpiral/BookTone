import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

class FakeClassList {
    #values = new Set();

    add(...values) {
        values.forEach(value => this.#values.add(value));
    }

    remove(...values) {
        values.forEach(value => this.#values.delete(value));
    }

    contains(value) {
        return this.#values.has(value);
    }
}

export class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.attributes = {};
        this.style = {};
        this.dataset = {};
        this.classList = new FakeClassList();
        this.listeners = new Map();
        this.value = '';
        this.textContent = '';
        this.innerHTML = '';
        this.ownerDocument = null;
        this.focused = false;
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    append(...children) {
        children.forEach(child => this.appendChild(child));
    }

    replaceChildren(...children) {
        this.children = [];
        this.append(...children);
    }

    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }

    dispatchEvent(event) {
        for (const handler of this.listeners.get(event.type) || []) {
            handler(event);
        }
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name];
    }

    querySelector() {
        return null;
    }

    closest(selector) {
        return selector === '[data-action]' && this.dataset.action ? this : null;
    }

    focus() {
        this.focused = true;
        if (this.ownerDocument) this.ownerDocument.activeElement = this;
    }
}

export class FakeDocument {
    constructor() {
        this.elements = new Map();
        this.listeners = new Map();
        this.body = new FakeElement('body');
        this.body.ownerDocument = this;
        this.activeElement = this.body;
    }

    register(id, element = new FakeElement()) {
        element.id = id;
        element.ownerDocument = this;
        this.elements.set(id, element);
        return element;
    }

    getElementById(id) {
        return this.elements.get(id) || null;
    }

    createElement(tagName) {
        const element = new FakeElement(tagName);
        element.ownerDocument = this;
        return element;
    }

    createTextNode(text) {
        const node = new FakeElement('#text');
        node.textContent = text;
        node.ownerDocument = this;
        return node;
    }

    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }

    dispatchEvent(event) {
        for (const handler of this.listeners.get(event.type) || []) {
            handler(event);
        }
    }

    querySelectorAll() {
        return [...this.elements.values()];
    }
}

export function createContext() {
    const document = new FakeDocument();
    const window = {
        addEventListener() {}
    };
    const context = vm.createContext({
        document,
        window,
        console,
        Element: FakeElement,
        Date,
        Map,
        Set,
        URLSearchParams,
        encodeURIComponent,
        setTimeout,
        clearTimeout
    });
    return { context, document, window };
}

export async function loadScript(context, relativePath) {
    const source = await readFile(new URL(`../../${relativePath}`, import.meta.url), 'utf8');
    vm.runInContext(source, context, { filename: relativePath });
}
