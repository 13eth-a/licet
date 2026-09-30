"""conservative dom evidence for known record grids, never layout tables"""
from html.parser import HTMLParser
import re

_EMPTY = re.compile(r'no (?:records?|results?|inspections?|fees?|documents?|data)(?:\b)|you have not added', re.I)


def grid_observations(html: str) -> list[dict]:
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack=[]
            self.grids=[]
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if tag=='table':
                identity=attrs.get('id','')
                relevant=bool(re.search('inspection|fee|attachment|condition|history|document|result',identity,re.I))
                self.stack.append({'id':identity,'rows':0,'text':'','row':False,'header':False,'relevant':relevant})
            if self.stack:
                if tag=='tr':
                    self.stack[-1].update(row=False,header=False)
                elif tag=='td':
                    self.stack[-1]['row']=True
                elif tag=='th':
                    self.stack[-1]['header']=True
        def handle_data(self, value):
            if self.stack:
                self.stack[-1]['text']+=value+' '
        def handle_endtag(self,tag):
            if not self.stack:
                return
            grid=self.stack[-1]
            if tag=='tr' and grid['row'] and not grid['header']:
                grid['rows']+=1
            if tag=='table':
                self.stack.pop()
                if grid['relevant']:
                    declared_empty=bool(_EMPTY.search(grid['text']))
                    self.grids.append({'id':grid['id'],'row_count':0 if declared_empty else grid['rows'],
                                       'declared_empty':declared_empty})
    parser=Parser()
    parser.feed(html or '')
    return parser.grids
