import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { createContext, FakeElement, loadScript } from './test-harness.mjs';

test('escapes HTML and attribute values', async () => {
    const { context } = createContext();
    await loadScript(context, 'frontend/static/js/util.js');

    assert.equal(
        vmCall(context, 'esc(`<script>"&</script>`)'),
        '&lt;script&gt;&quot;&amp;&lt;/script&gt;'
    );
    assert.equal(vmCall(context, String.raw`escAttr("a'b\\c\nd")`), "a\\'b\\\\c\\nd");
});

test('bindActionHandlers dispatches declarative actions', async () => {
    const { context, document } = createContext();
    await loadScript(context, 'frontend/static/js/util.js');

    vmCall(context, `globalThis.calls = [];
        bindActionHandlers({
            click: { save: (event, target) => globalThis.calls.push([event.type, target.dataset.action]) }
        })`);

    const target = new FakeElement('button');
    target.dataset.action = 'save';
    const eventTarget = { type: 'click', target };
    document.dispatchEvent(eventTarget);

    assert.deepEqual(JSON.parse(JSON.stringify(context.calls)), [['click', 'save']]);
});

test('formats time with hours only when needed', async () => {
    const { context, document } = createContext();
    document.register('loadingOverlay');
    await loadScript(context, 'frontend/static/js/stream-state.js');

    assert.equal(vmCall(context, 'formatTime(0)'), '0:00');
    assert.equal(vmCall(context, 'formatTime(65)'), '1:05');
    assert.equal(vmCall(context, 'formatTime(3661)'), '1:01:01');
});

test('renders breadcrumbs with safe text nodes and navigation callbacks', async () => {
    const { context, document } = createContext();
    document.register('filesTab', new FakeElement('button'));
    document.register('modelsTab', new FakeElement('button'));
    await loadScript(context, 'frontend/static/js/util.js');
    await loadScript(context, 'frontend/static/js/app.js');

    const container = document.createElement('nav');
    const paths = [];
    context.paths = paths;
    context.container = container;
    vmCall(context, `renderBreadcrumb(container, 'books/fiction', path => paths.push(path), 'Home')`);

    assert.equal(container.children[0].textContent, 'Home');
    assert.equal(container.children[2].textContent, 'books');
    assert.equal(container.children[4].textContent, 'fiction');
    const links = container.children.filter(child => child.tagName === 'SPAN');
    assert.equal(links.at(-1).textContent, 'fiction');
    links.at(-1).onclick();
    assert.deepEqual(Array.from(paths), ['books/fiction']);
});

test('sorts directories first and applies requested file ordering', async () => {
    const { context, document } = createContext();
    for (const id of ['filesTab', 'modelsTab']) document.register(id, new FakeElement('button'));
    await loadScript(context, 'frontend/static/js/util.js');
    await loadScript(context, 'frontend/static/js/app.js');
    await loadScript(context, 'frontend/static/js/file-manager.js');

    const files = [
        { name: 'z.txt', path: 'z.txt', is_directory: false, modified: 30 },
        { name: 'a-folder', path: 'a-folder', is_directory: true, modified: 1 },
        { name: 'a.txt', path: 'a.txt', is_directory: false, modified: 20 }
    ];
    context.files = files;
    const sorted = vmCall(context, 'applySortToFiles(files, "name")');

    assert.deepEqual(Array.from(sorted, file => file.name), ['a-folder', 'a.txt', 'z.txt']);
    assert.deepEqual(Array.from(files, file => file.name), ['z.txt', 'a-folder', 'a.txt']);
});

test('frontend templates keep behavior out of inline event attributes', async () => {
    const [index, stream] = await Promise.all([
        readFile(new URL('../templates/index.html', import.meta.url), 'utf8'),
        readFile(new URL('../templates/stream.html', import.meta.url), 'utf8')
    ]);

    assert.doesNotMatch(`${index}\n${stream}`, /\bon(?:click|change|input|submit|mousedown|mouseup|touchstart|touchend)=/i);
    assert.match(stream, /\/static\/css\/stream\.css/);
    assert.match(stream, /data-action="progress"/);
});

function vmCall(context, expression) {
    return vm.runInContext(expression, context);
}
