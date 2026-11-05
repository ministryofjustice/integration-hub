$(function () {
  'use strict'

  var navigation = document.querySelector('#toc')
  if (!navigation) return

  function normalisePath (path) {
    return path.replace(/\/index\.html$/, '/')
  }

  function isCurrentPage (link) {
    var url = new URL(link.href, window.location.href)
    return url.origin === window.location.origin &&
      normalisePath(url.pathname) === normalisePath(window.location.pathname) &&
      !url.hash
  }

  // Use the rendered page's IDs: the publisher can generate incorrect
  // heading links when it renders other pages for the navigation.
  Array.prototype.some.call(navigation.querySelectorAll('li > a[href]'), function (link) {
    if (!isCurrentPage(link)) return false

    if (new URL(link.href, window.location.href).pathname !== window.location.pathname) {
      link.href = window.location.pathname
    }

    var item = link.parentElement
    var list
    Array.prototype.forEach.call(item.children, function (child) {
      if (child.tagName === 'UL') list = child
    })
    var headings = document.querySelectorAll('#content h2[id]')
    if (!headings.length) return true

    var childPages = list ? Array.prototype.map.call(list.children, function (child) {
      var childLink = child.querySelector('a[href]')
      return childLink && new URL(childLink.href, window.location.href).href
    }) : []
    var sectionList = list || document.createElement('ul')
    Array.prototype.forEach.call(headings, function (heading) {
      var linksToChild = Array.prototype.some.call(heading.querySelectorAll('a[href]'), function (headingLink) {
        return childPages.indexOf(new URL(headingLink.href, window.location.href).href) !== -1
      })
      if (linksToChild) return

      var section = document.createElement('li')
      var sectionLink = document.createElement('a')
      sectionLink.href = '#' + encodeURIComponent(heading.id)
      sectionLink.textContent = heading.textContent.trim()
      section.appendChild(sectionLink)
      sectionList.appendChild(section)
    })
    if (!list && sectionList.children.length) item.appendChild(sectionList)
    return true
  })

  var items = navigation.querySelectorAll('li')
  Array.prototype.forEach.call(items, function (item, index) {
    if (item.parentElement.parentElement === navigation) return

    var link
    var list
    Array.prototype.forEach.call(item.children, function (child) {
      if (child.tagName === 'A') link = child
      if (child.tagName === 'UL') list = child
    })
    if (!link || !list) return

    var isOpen = Array.prototype.some.call(item.querySelectorAll('a[href]'), isCurrentPage)
    var button = document.createElement('button')
    var label = document.createElement('span')
    var title = link.textContent.trim()

    list.id = 'subsection-navigation-' + index
    list.classList.add('subsection-navigation__list')
    item.classList.add('subsection-navigation__item')
    button.type = 'button'
    button.className = 'subsection-navigation__toggle'
    button.setAttribute('aria-controls', list.id)
    label.className = 'govuk-visually-hidden'
    button.appendChild(label)

    function setOpen (open) {
      list.hidden = !open
      button.setAttribute('aria-expanded', String(open))
      label.textContent = (open ? 'Collapse ' : 'Expand ') + title
    }

    setOpen(isOpen)
    button.addEventListener('click', function () {
      setOpen(list.hidden)
    })
    link.insertAdjacentElement('afterend', button)
  })
})
