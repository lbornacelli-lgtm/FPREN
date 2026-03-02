# services/xml_parser.py
import xml.etree.ElementTree as ET
import logging

class XMLParser:
    def parse(self, xml_content: str) -> str:
        """Parse XML string and return readable text"""
        try:
            root = ET.fromstring(xml_content)
            texts = []

            # Recursive XML text extraction
            def recurse(node):
                if node.text and node.text.strip():
                    texts.append(node.text.strip())
                for child in node:
                    recurse(child)

            recurse(root)
            parsed_text = " ".join(texts)
            logging.info("XML parsed successfully")
            return parsed_text
        except ET.ParseError as e:
            logging.error(f"XML parsing error: {e}")
            return ""
