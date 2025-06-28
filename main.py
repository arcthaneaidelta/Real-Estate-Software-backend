# main.py - Improved FastAPI application for Railway deployment
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import json
import time
import random
from urllib.parse import quote, urlencode
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import re
from datetime import datetime, timedelta
import os
import uvicorn
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Property:
    address: str
    bedrooms: int
    bathrooms: float
    square_feet: int
    price: int
    url: str
    status: str = "for_sale"
    sold_date: Optional[str] = None
    property_type: str = "house"

@dataclass
class MapBounds:
    west: float
    east: float
    south: float
    north: float

class ZillowRealEstateAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"'
        })
        # Add delay between requests to avoid rate limiting
        self.request_delay = 2
    
    def find_subject_property_and_comps(self, city: str, state: str, min_price: int, max_price: int, map_bounds: Optional[MapBounds] = None) -> Dict[str, Any]:
        try:
            logger.info(f"Searching for properties in {city}, {state} with price range ${min_price:,} - ${max_price:,}")
            
            # First, try to get the location ID for the city
            location_info = self._get_location_info(city, state)
            if not location_info:
                logger.warning(f"Could not find location info for {city}, {state}")
                return {
                    "error": f"Could not find location information for {city}, {state}",
                    "subject_property": None,
                    "comparables": [],
                    "total_comps_found": 0
                }
            
            # Search for active listings (subject property)
            subject_properties = self._search_properties(location_info, min_price, max_price, "for_sale")
            subject_property = subject_properties[0] if subject_properties else None
            
            if not subject_property:
                logger.warning(f"No active listings found in {city}, {state}")
            
            # Search for sold properties (comparables)
            comparables = self._search_properties(location_info, min_price, max_price, "sold", limit=10)
            
            logger.info(f"Found {len(comparables)} comparable properties")
            
            return {
                "subject_property": self._format_property_output(subject_property) if subject_property else None,
                "comparables": [self._format_property_output(comp, is_comp=True) for comp in comparables],
                "total_comps_found": len(comparables),
                "location_info": location_info  # Include for debugging
            }
            
        except Exception as e:
            logger.error(f"API Error: {str(e)}")
            return {
                "error": f"API Error: {str(e)}",
                "subject_property": None,
                "comparables": [],
                "total_comps_found": 0
            }
    
    def _get_location_info(self, city: str, state: str) -> Optional[Dict]:
        """Get location information including region ID and proper URL format"""
        try:
            # Try different URL formats for the city based on current Zillow structure
            city_formatted = city.lower().replace(' ', '-')
            state_formatted = state.lower()
            
            possible_urls = [
                f"https://www.zillow.com/homes/{city_formatted}-{state_formatted}_rb/",
                f"https://www.zillow.com/{city_formatted}-{state_formatted}/",
                f"https://www.zillow.com/homes/{city_formatted}_{state_formatted}_rb/",
                f"https://www.zillow.com/{city_formatted}-{state_formatted}-{state_formatted}/",
                f"https://www.zillow.com/homes/for_sale/{city_formatted}-{state_formatted}/",
            ]
            
            for url in possible_urls:
                logger.info(f"Trying URL: {url}")
                time.sleep(self.request_delay)
                
                try:
                    response = self.session.get(url, timeout=15)
                    logger.info(f"Response status: {response.status_code} for URL: {url}")
                    
                    if response.status_code == 200:
                        # Check if the page contains property listings or region data
                        if self._validate_zillow_page(response.text):
                            region_info = self._extract_region_info(response.text, url)
                            if region_info:
                                logger.info(f"Successfully found location info for {city}, {state}")
                                return region_info
                            else:
                                # Even if we can't extract region info, if it's a valid page, use it
                                logger.info(f"Valid Zillow page found, using basic info for {city}, {state}")
                                return {
                                    'region_id': 0,
                                    'region_type': 6,
                                    'base_url': url,
                                    'region_name': f"{city}, {state}",
                                    'state_abbreviation': state
                                }
                    elif response.status_code == 404:
                        logger.info(f"URL not found: {url}")
                    else:
                        logger.warning(f"Unexpected status code {response.status_code} for {url}")
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch {url}: {str(e)}")
                    continue
            
            logger.warning(f"Could not find valid URL for {city}, {state}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting location info: {str(e)}")
            return None
    
    def _validate_zillow_page(self, html_content: str) -> bool:
        """Check if the page is a valid Zillow property search page"""
        try:
            # Check for common Zillow page elements
            indicators = [
                'zillow.com',
                'property',
                'real estate',
                'homes for sale',
                'searchPageState',
                'listResults',
                'property-card',
                'SearchPageState'
            ]
            
            html_lower = html_content.lower()
            found_indicators = sum(1 for indicator in indicators if indicator in html_lower)
            
            # If we find at least 3 indicators, consider it a valid Zillow page
            is_valid = found_indicators >= 3
            logger.info(f"Page validation: {found_indicators}/{len(indicators)} indicators found, valid: {is_valid}")
            
            return is_valid
            
    
    def _extract_region_info(self, html_content: str, base_url: str) -> Optional[Dict]:
        """Extract region information from Zillow page"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Look for JSON data in script tags
            script_tags = soup.find_all('script', type='application/json')
            for script in script_tags:
                try:
                    data = json.loads(script.string)
                    if 'props' in data and 'pageProps' in data['props']:
                        page_props = data['props']['pageProps']
                        if 'regionId' in page_props:
                            return {
                                'region_id': page_props['regionId'],
                                'region_type': page_props.get('regionType', 6),
                                'base_url': base_url,
                                'region_name': page_props.get('regionName', ''),
                                'state_abbreviation': page_props.get('stateAbbreviation', '')
                            }
                except (json.JSONDecodeError, KeyError):
                    continue
            
            # Fallback: look for region data in other script tags
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'regionId' in script.string:
                    try:
                        # Try to extract region ID using regex
                        region_match = re.search(r'"regionId":(\d+)', script.string)
                        region_type_match = re.search(r'"regionType":(\d+)', script.string)
                        
                        if region_match:
                            return {
                                'region_id': int(region_match.group(1)),
                                'region_type': int(region_type_match.group(1)) if region_type_match else 6,
                                'base_url': base_url,
                                'region_name': '',
                                'state_abbreviation': ''
                            }
                    except Exception as e:
                        continue
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting region info: {str(e)}")
            return None
    
    def _search_properties(self, location_info: Dict, min_price: int, max_price: int, status: str, limit: int = 10) -> List[Property]:
        """Search for properties using Zillow's search page"""
        try:
            base_url = location_info['base_url']
            
            # Build search URL with filters
            if status == "for_sale":
                # For active listings
                if base_url.endswith('_rb/'):
                    search_url = base_url[:-4] + f"/{min_price}-{max_price}_price/_rb/"
                else:
                    search_url = base_url.rstrip('/') + f"/{min_price}-{max_price}_price/"
            else:
                # For sold properties
                if base_url.endswith('_rb/'):
                    search_url = base_url[:-4] + f"/sold/{min_price}-{max_price}_price/_rb/"
                else:
                    search_url = base_url.rstrip('/') + f"/sold/{min_price}-{max_price}_price/"
            
            logger.info(f"Searching: {search_url}")
            
            time.sleep(self.request_delay)
            response = self.session.get(search_url, timeout=15)
            
            if response.status_code != 200:
                # Try alternative URL format
                alt_url = f"https://www.zillow.com/homes/for_sale/{min_price}-{max_price}_price/?searchQueryState=%7B%22pagination%22%3A%7B%7D%2C%22isMapVisible%22%3Atrue%7D"
                logger.info(f"Trying alternative URL: {alt_url}")
                time.sleep(self.request_delay)
                response = self.session.get(alt_url, timeout=15)
            
            if response.status_code != 200:
                logger.warning(f"Search request failed with status {response.status_code}")
                return []
            
            properties = self._parse_search_results(response.text, status)
            logger.info(f"Parsed {len(properties)} properties from search results")
            
            return properties[:limit]
            
        except Exception as e:
            logger.error(f"Error searching properties: {str(e)}")
            return []
    
    def _build_search_params(self, location_info: Dict, min_price: int, max_price: int, status: str) -> Dict:
        """Build search parameters for Zillow URL"""
        params = {}
        
        if status == "for_sale":
            params.update({
                'price_min': min_price,
                'price_max': max_price,
                'home_type': 'Houses,Condos,Townhomes',
                'for_sale': 'true'
            })
        elif status == "sold":
            params.update({
                'price_min': min_price,
                'price_max': max_price,
                'home_type': 'Houses,Condos,Townhomes',
                'sold_within': '3mo'  # Last 3 months
            })
        
        return params
    
    def _parse_search_results(self, html_content: str, status: str) -> List[Property]:
        """Parse property listings from search results page"""
        try:
            properties = []
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Look for property cards using various selectors
            property_selectors = [
                'article[data-test="property-card"]',
                '.property-card-data',
                '.list-card-info',
                '[data-test="property-card"]',
                '.PropertyCard',
                '.result-list-container article'
            ]
            
            for selector in property_selectors:
                cards = soup.select(selector)
                if cards:
                    logger.info(f"Found {len(cards)} property cards using selector: {selector}")
                    for card in cards:
                        prop = self._extract_property_from_card(card, status)
                        if prop and prop.price > 0:  # Only add properties with valid prices
                            properties.append(prop)
                    break
            
            # If no properties found, try alternative parsing methods
            if not properties:
                properties = self._parse_alternative_format(soup, status)
            
            return properties
            
        except Exception as e:
            logger.error(f"Error parsing search results: {str(e)}")
            return []
    
    def _extract_property_from_card(self, card, status: str) -> Optional[Property]:
        """Extract property information from a property card element"""
        try:
            # Extract price
            price_selectors = [
                '[data-test="property-card-price"]',
                '.PropertyCardWrapper__StyledPriceLine',
                '.list-card-price',
                '.price'
            ]
            
            price = 0
            for selector in price_selectors:
                price_elem = card.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price = self._parse_price(price_text)
                    if price > 0:
                        break
            
            # Extract address
            address_selectors = [
                '[data-test="property-card-addr"]',
                '.PropertyCardWrapper__StyledAddress',
                '.list-card-addr',
                '.address'
            ]
            
            address = "Address not available"
            for selector in address_selectors:
                addr_elem = card.select_one(selector)
                if addr_elem:
                    address = addr_elem.get_text(strip=True)
                    break
            
            # Extract details (beds, baths, sqft)
            details_selectors = [
                '[data-test="property-card-details"]',
                '.PropertyCardWrapper__StyledPropertyDetails',
                '.list-card-details',
                '.property-details'
            ]
            
            bedrooms = 0
            bathrooms = 0
            square_feet = 0
            
            for selector in details_selectors:
                details_elem = card.select_one(selector)
                if details_elem:
                    details_text = details_elem.get_text(strip=True)
                    bedrooms = self._extract_bedrooms(details_text)
                    bathrooms = self._extract_bathrooms(details_text)
                    square_feet = self._extract_square_feet(details_text)
                    break
            
            # Extract URL
            url = "https://www.zillow.com"
            link_elem = card.find('a', href=True)
            if link_elem and link_elem.get('href'):
                href = link_elem['href']
                if href.startswith('/'):
                    url = f"https://www.zillow.com{href}"
                elif href.startswith('http'):
                    url = href
            
            # Only return property if we have valid data
            if price > 0 and address != "Address not available":
                return Property(
                    address=address,
                    bedrooms=bedrooms,
                    bathrooms=bathrooms,
                    square_feet=square_feet,
                    price=price,
                    url=url,
                    status=status
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting property from card: {str(e)}")
            return None
    
    def _parse_alternative_format(self, soup: BeautifulSoup, status: str) -> List[Property]:
        """Alternative parsing method for different page formats"""
        properties = []
        
        # Look for script tags with JSON data
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and ('listResults' in script.string or 'searchResults' in script.string):
                try:
                    # Try to extract JSON data
                    json_match = re.search(r'(\{.*"listResults".*?\})', script.string)
                    if json_match:
                        data = json.loads(json_match.group(1))
                        if 'listResults' in data:
                            for listing in data['listResults']:
                                prop = self._create_property_from_json(listing, status)
                                if prop:
                                    properties.append(prop)
                except Exception as e:
                    continue
        
        return properties
    
    def _create_property_from_json(self, listing_data: Dict, status: str) -> Optional[Property]:
        """Create Property object from JSON listing data"""
        try:
            return Property(
                address=listing_data.get('address', 'Address not available'),
                bedrooms=listing_data.get('beds', 0),
                bathrooms=listing_data.get('baths', 0),
                square_feet=listing_data.get('area', 0),
                price=listing_data.get('price', 0) or listing_data.get('unformattedPrice', 0),
                url=f"https://www.zillow.com{listing_data.get('detailUrl', '')}",
                status=status,
                sold_date=listing_data.get('dateSold') if status == 'sold' else None
            )
        except Exception as e:
            logger.error(f"Error creating property from JSON: {str(e)}")
            return None
    
    def _parse_price(self, price_text: str) -> int:
        """Parse price from text"""
        if not price_text:
            return 0
        
        # Remove common price prefixes and suffixes
        price_text = re.sub(r'[^\d,]', '', price_text)
        price_numbers = re.findall(r'[\d,]+', price_text)
        
        if price_numbers:
            try:
                return int(price_numbers[0].replace(',', ''))
            except ValueError:
                return 0
        return 0
    
    def _extract_bedrooms(self, text: str) -> int:
        """Extract number of bedrooms from details text"""
        patterns = [
            r'(\d+)\s*(?:bd|bed|bedroom)s?',
            r'(\d+)\s*(?:BR|br)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return 0
    
    def _extract_bathrooms(self, text: str) -> float:
        """Extract number of bathrooms from details text"""
        patterns = [
            r'(\d+\.?\d*)\s*(?:ba|bath|bathroom)s?',
            r'(\d+\.?\d*)\s*(?:BA|ba)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return 0
    
    def _extract_square_feet(self, text: str) -> int:
        """Extract square footage from details text"""
        patterns = [
            r'([\d,]+)\s*(?:sq\.?\s*ft|sqft|square\s*feet)',
            r'([\d,]+)\s*(?:SF|sf)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1).replace(',', ''))
                except ValueError:
                    continue
        return 0
    
    def _format_property_output(self, property_obj: Property, is_comp: bool = False) -> str:
        """Format property information for output"""
        if not property_obj:
            return "No property data available"
        
        bed_text = f"{property_obj.bedrooms} bed" if property_obj.bedrooms > 0 else "beds N/A"
        bath_text = f"{property_obj.bathrooms} bath" if property_obj.bathrooms > 0 else "baths N/A"
        sqft_text = f"{property_obj.square_feet:,} sq ft" if property_obj.square_feet > 0 else "sq ft N/A"
        
        if is_comp and property_obj.status == "sold":
            price_text = f"sold for ${property_obj.price:,}"
        else:
            price_text = f"${property_obj.price:,}"
        
        return f"{property_obj.address} - {bed_text}, {bath_text}, {sqft_text} - {price_text} - {property_obj.url}"

# FastAPI Application
app = FastAPI(title="Improved Zillow Real Estate API", version="2.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize API
zillow_api = ZillowRealEstateAPI()

# Pydantic models for request/response
class MapBoundsModel(BaseModel):
    west: float
    east: float
    south: float
    north: float

class PropertySearchRequest(BaseModel):
    city: str
    state: str
    min_price: int
    max_price: int
    map_bounds: Optional[MapBoundsModel] = None

class PropertySearchResponse(BaseModel):
    subject_property: Optional[str] = None
    comparables: List[str] = []
    total_comps_found: int = 0
    error: Optional[str] = None
    location_info: Optional[Dict] = None

@app.get("/")
async def root():
    return {"message": "Improved Zillow Real Estate API", "version": "2.0.0"}

@app.get("/search")
async def search_properties(
    city: str = Query(..., description="City name"),
    state: str = Query(..., description="State abbreviation (e.g., CA, NY)"),
    min_price: int = Query(..., description="Minimum price in dollars"),
    max_price: int = Query(..., description="Maximum price in dollars"),
    west: Optional[float] = Query(None, description="Western longitude boundary"),
    east: Optional[float] = Query(None, description="Eastern longitude boundary"),
    south: Optional[float] = Query(None, description="Southern latitude boundary"),
    north: Optional[float] = Query(None, description="Northern latitude boundary")
):
    """
    Search for subject property and comparable properties
    """
    try:
        # Create map bounds if all coordinates are provided
        map_bounds = None
        if all(coord is not None for coord in [west, east, south, north]):
            map_bounds = MapBounds(west=west, east=east, south=south, north=north)
        
        results = zillow_api.find_subject_property_and_comps(city, state, min_price, max_price, map_bounds)
        return PropertySearchResponse(**results)
    except Exception as e:
        logger.error(f"Search endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/search")
async def search_properties_post(request: PropertySearchRequest):
    """
    Search for subject property and comparable properties (POST method)
    """
    try:
        # Convert Pydantic model to dataclass if provided
        map_bounds = None
        if request.map_bounds:
            map_bounds = MapBounds(
                west=request.map_bounds.west,
                east=request.map_bounds.east,
                south=request.map_bounds.south,
                north=request.map_bounds.north
            )
        
        results = zillow_api.find_subject_property_and_comps(
            request.city, request.state, request.min_price, request.max_price, map_bounds
        )
        return PropertySearchResponse(**results)
    except Exception as e:
        logger.error(f"Search POST endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/debug/{city}/{state}")
async def debug_location(city: str, state: str):
    """Debug endpoint to see what's happening with location detection"""
    try:
        import time
        
        city_formatted = city.lower().replace(' ', '-')
        state_formatted = state.lower()
        
        test_urls = [
            f"https://www.zillow.com/homes/{city_formatted}-{state_formatted}_rb/",
            f"https://www.zillow.com/{city_formatted}-{state_formatted}/",
            f"https://www.zillow.com/homes/{city_formatted}_{state_formatted}_rb/",
        ]
        
        results = []
        
        for url in test_urls:
            try:
                response = requests.get(url, timeout=10)
                content_preview = response.text[:500] if response.text else "No content"
                
                results.append({
                    "url": url,
                    "status_code": response.status_code,
                    "content_length": len(response.text) if response.text else 0,
                    "content_preview": content_preview,
                    "has_zillow_indicators": any(indicator in response.text.lower() for indicator in ['zillow', 'property', 'real estate']) if response.text else False
                })
                time.sleep(1)  # Delay between requests
            except Exception as e:
                results.append({
                    "url": url,
                    "error": str(e)
                })
        
        return {"city": city, "state": state, "test_results": results}
    except Exception as e:
        return {"error": str(e)}

# Railway deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
