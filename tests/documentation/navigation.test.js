const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const script = fs.readFileSync(
  path.join(__dirname, '../../source/javascripts/subsection-navigation.js'),
  'utf8'
)

class Element {
  constructor (tag) {
    this.tagName = tag.toUpperCase()
    this.children = []
    this.attributes = {}
    this.events = {}
    this.classList = { add () {} }
    this.hidden = false
    this.text = ''
  }

  set textContent (text) { this.text = text }
  get textContent () { return this.text + this.children.map(child => child.textContent).join('') }

  appendChild (child) {
    child.parentElement = this
    this.children.push(child)
    return child
  }

  insertAdjacentElement (position, element) {
    assert.equal(position, 'afterend')
    element.parentElement = this.parentElement
    const siblings = this.parentElement.children
    siblings.splice(siblings.indexOf(this) + 1, 0, element)
  }

  setAttribute (name, value) { this.attributes[name] = value }
  getAttribute (name) { return this.attributes[name] }
  addEventListener (name, callback) { this.events[name] = callback }
  click () { this.events.click() }
  querySelector (selector) { return this.querySelectorAll(selector)[0] }

  querySelectorAll (selector) {
    const descendants = []
    function collect (element) {
      for (const child of element.children) {
        descendants.push(child)
        collect(child)
      }
    }
    collect(this)
    if (selector === 'li') return descendants.filter(element => element.tagName === 'LI')
    if (selector === 'a[href]') return descendants.filter(element => element.tagName === 'A' && element.href)
    if (selector === 'li > a[href]') {
      return descendants.filter(element =>
        element.tagName === 'A' && element.href && element.parentElement.tagName === 'LI'
      )
    }
    throw new Error('Unexpected selector: ' + selector)
  }
}

function item (list, title, href) {
  const entry = list.appendChild(new Element('li'))
  const link = entry.appendChild(new Element('a'))
  link.textContent = title
  link.href = href
  return entry
}

function fixture (headings = ['First section', 'Second section']) {
  const nav = new Element('nav')
  const root = nav.appendChild(new Element('ul'))
  const service = item(root, 'File transfer service', '/file-transfer-service/index.html')
  const serviceList = service.appendChild(new Element('ul'))
  const guide = item(serviceList, 'User guidance', '/file-transfer-service/user-guide/index.html')
  const guideList = guide.appendChild(new Element('ul'))
  const current = item(guideList, 'Current page', '/file-transfer-service/user-guide/current.html')
  const other = item(guideList, 'Other page', '/file-transfer-service/user-guide/other.html')
  const otherGroup = item(serviceList, 'Service guidance', '/file-transfer-service/operations/index.html')
  otherGroup.appendChild(new Element('ul'))
  const content = headings.map((title, index) => {
    const heading = new Element('h2')
    heading.id = 'section-' + (index + 1)
    heading.textContent = title
    return heading
  })
  return { nav, guide, current, other, otherGroup, content }
}

function initialise (navigation, content, pathname = '/file-transfer-service/user-guide/current.html') {
  const ready = []
  const document = {
    querySelector: selector => selector === '#toc' ? navigation : null,
    querySelectorAll: selector => {
      assert.equal(selector, '#content h2[id]')
      return content
    },
    createElement: tag => new Element(tag)
  }
  vm.runInNewContext(script, {
    $: callback => ready.push(callback),
    document,
    window: { location: { href: 'https://example.test' + pathname, origin: 'https://example.test', pathname } },
    URL
  })
  ready.forEach(callback => callback())
}

function child (element, tag) {
  return element.children.find(entry => entry.tagName === tag.toUpperCase())
}

test('opens the current path and links to the rendered page headings', () => {
  const { nav, guide, current, other, otherGroup, content } = fixture()
  initialise(nav, content)

  assert.equal(child(guide, 'button').getAttribute('aria-expanded'), 'true')
  const button = child(current, 'button')
  const list = child(current, 'ul')
  assert.equal(button.getAttribute('aria-expanded'), 'true')
  assert.equal(button.getAttribute('aria-controls'), list.id)
  assert.equal(list.hidden, false)
  assert.deepEqual(list.querySelectorAll('a[href]').map(link => link.href), ['#section-1', '#section-2'])
  assert.equal(child(other, 'ul'), undefined)
  assert.equal(child(otherGroup, 'button').getAttribute('aria-expanded'), 'false')
})

test('toggles a subsection independently and updates its accessible state', () => {
  const { nav, guide, current, content } = fixture()
  initialise(nav, content)
  const button = child(current, 'button')
  const list = child(current, 'ul')

  button.click()
  assert.equal(list.hidden, true)
  assert.equal(button.getAttribute('aria-expanded'), 'false')
  assert.equal(button.textContent, 'Expand Current page')
  assert.equal(child(guide, 'button').getAttribute('aria-expanded'), 'true')

  button.click()
  assert.equal(list.hidden, false)
  assert.equal(button.getAttribute('aria-expanded'), 'true')
  assert.equal(button.textContent, 'Collapse Current page')
})

test('leaves pages without headings and unrelated paths unchanged', () => {
  const empty = fixture([])
  initialise(empty.nav, empty.content)
  assert.equal(child(empty.current, 'ul'), undefined)
  assert.equal(child(empty.current, 'button'), undefined)

  const unrelated = fixture()
  initialise(unrelated.nav, unrelated.content, '/another-page.html')
  assert.equal(child(unrelated.current, 'ul'), undefined)
  assert.equal(child(unrelated.guide, 'button').getAttribute('aria-expanded'), 'false')
})

test('adds headings to a top-level page for the publisher to make collapsible', () => {
  const nav = new Element('nav')
  const page = item(nav.appendChild(new Element('ul')), 'Home', '/index.html')
  const heading = new Element('h2')
  heading.id = 'section 1'
  heading.textContent = 'First section'
  initialise(nav, [heading], '/')
  assert.equal(child(page, 'a').href, '/')
  assert.equal(child(page, 'button'), undefined)
  assert.equal(child(page, 'ul').querySelectorAll('a[href]')[0].href, '#section%201')
})

test('adds index-page headings without duplicating linked child pages', () => {
  const { nav, guide, content } = fixture(['Overview', 'Current page'])
  const linkedPage = content[1].appendChild(new Element('a'))
  linkedPage.href = '/file-transfer-service/user-guide/current.html'
  initialise(nav, content, '/file-transfer-service/user-guide/index.html')
  const links = child(guide, 'ul').querySelectorAll('a[href]')
  assert.deepEqual(links.map(link => link.href), [
    '/file-transfer-service/user-guide/current.html',
    '/file-transfer-service/user-guide/other.html',
    '#section-1'
  ])
  assert.equal(child(guide, 'button').getAttribute('aria-expanded'), 'true')
})

test('does nothing when there is no navigation', () => {
  assert.doesNotThrow(() => initialise(null, []))
})
