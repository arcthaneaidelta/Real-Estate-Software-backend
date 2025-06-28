# main.py - Updated FastAPI application for Railway deployment with modern Zillow URL structure
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import json
import time
import random
from urllib.parse import quote, urlencode, unquote
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
        
        # Default map bounds for major cities (you can expand this)
        self.city_bounds = {
            'san francisco': {'west': -122.61529055957031, 'east': -122.25136844042969, 'south': 37.66231632707035, 'north': 37.8880952040113},
            'los angeles': {'west': -118.6681759, 'east': -117.9441986, 'south': 33.7036917, 'north': 34.3373061},
            'new york': {'west': -74.2590879, 'east': -73.7004681, 'south': 40.4774, 'north': 40.9176},
            'chicago': {'west': -87.9401, 'east': -87.5241, 'south': 41.6445, 'north': 42.0230},
            'miami': {'west': -80.8738, 'east': -80.1166, 'south': 25.6634, 'north': 25.8553}
        }
    
    def find_subject_property_and_comps(self, city: str, state: str, min_price: int, max_price: int, map_bounds: Optional[MapBounds] = None) -> Dict[str, Any]:
        try:
            logger.info(f"Searching for properties in {city}, {state} with price range ${min_price:,} - ${max_price:,}")
            
            # Get region information and build modern URL
            region_info = self._get_region_info(city, state)
            if not region_info:
                logger.warning(f"Could not find region info for {city}, {state}")
                return {
                    "error": f"Could not find region information for {city}, {state}",
                    "subject_property": None,
                    "comparables": [],
                    "total_comps_found": 0
                }
            
            # Use provided map bounds or default ones
            if not map_bounds:
                city_key = city.lower().replace(' ', ' ')
                if city_key in self.city_bounds:
                    bounds = self.city_bounds[city_key]
                    map_bounds = MapBounds(
                        west=bounds['west'],
                        east=bounds['east'],
                        south=bounds['south'],
                        north=bounds['north']
                    )
                else:
                    # Default bounds (approximate US bounds)
                    map_bounds = MapBounds(west=-125, east=-66, south=25, north=49)
            
            # Search for active listings (subject property)
            subject_properties = self._search_properties_modern(region_info, min_price, max_price, map_bounds, "for_sale")
            subject_property = subject_properties[0] if subject_properties else None
            
            if not subject_property:
                logger.warning(f"No active listings found in {city}, {state}")
            
            # Search for sold properties (comparables)
            comparables = self._search_properties_modern(region_info, min_price, max_price, map_bounds, "sold", limit=10)
            
            logger.info(f"Found {len(comparables)} comparable properties")
            
            return {
                "subject_property": self._format_property_output(subject_property) if subject_property else None,
                "comparables": [self._format_property_output(comp, is_comp=True) for comp in comparables],
                "total_comps_found": len(comparables),
                "region_info": region_info  # Include for debugging
            }
            
        except Exception as e:
            logger.error(f"API Error: {str(e)}")
            return {
                "error": f"API Error: {str(e)}",
                "subject_property": None,
                "comparables": [],
                "total_comps_found": 0
            }
    
    def _get_region_info(self, city: str, state: str) -> Optional[Dict]:
        """Get region information by trying the basic city page first"""
        try:
            # Format city name for URL
            city_formatted = city.lower().replace(' ', '-')
            state_formatted = state.lower()
            
            # Try the modern URL structure first
            base_url = f"https://www.zillow.com/{city_formatted}-{state_formatted}/"
            
            logger.info(f"Trying to get region info from: {base_url}")
            time.sleep(self.request_delay)
            
            response = self.session.get(base_url, timeout=15)
            logger.info(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                region_info = self._extract_region_info_modern(response.text, city, state)
                if region_info:
                    region_info['base_url'] = base_url
                    return region_info
            
            # Fallback: try alternative formats
            alternative_urls = [
                f"https://www.zillow.com/homes/{city_formatted}-{state_formatted}_rb/",
                f"https://www.zillow.com/{city_formatted}_{state_formatted}/",
                f"https://www.zillow.com/homes/for_sale/{city_formatted}-{state_formatted}/",
            ]
            
            for url in alternative_urls:
                try:
                    logger.info(f"Trying alternative URL: {url}")
                    time.sleep(self.request_delay)
                    response = self.session.get(url, timeout=15)
                    
                    if response.status_code == 200:
                        region_info = self._extract_region_info_modern(response.text, city, state)
                        if region_info:
                            region_info['base_url'] = url
                            return region_info
                except Exception as e:
                    logger.warning(f"Failed to fetch {url}: {str(e)}")
                    continue
            
            # If all else fails, create a basic region info
            logger.warning(f"Could not extract region info, using default for {city}, {state}")
            return {
                'region_id': 0,
                'region_type': 6,
                'base_url': base_url,
                'region_name': f"{city}, {state}",
                'city': city,
                'state': state
            }
            
        except Exception as e:
            logger.error(f"Error getting region info: {str(e)}")
            return None
    
    def _extract_region_info_modern(self, html_content: str, city: str, state: str) -> Optional[Dict]:
        """Extract region information from modern Zillow page"""
        try:
            # Look for region data in script tags
            region_patterns = [
                r'"regionId":(\d+)',
                r'"regionType":(\d+)',
                r'regionId.*?(\d+)',
                r'regionType.*?(\d+)'
            ]
            
            region_id = None
            region_type = 6  # Default region type
            
            # Try to find region ID
            for pattern in region_patterns[:2]:  # First two are more specific
                match = re.search(pattern, html_content)
                if match:
                    if 'regionId' in pattern:
                        region_id = int(match.group(1))
                    elif 'regionType' in pattern:
                        region_type = int(match.group(1))
            
            # If we found a region ID, use it
            if region_id:
                return {
                    'region_id': region_id,
                    'region_type': region_type,
                    'region_name': f"{city}, {state}",
                    'city': city,
                    'state': state
                }
            
            # Fallback: look for any numeric ID that might be region-related
            id_patterns = [
                r'"id":(\d{4,})',  # Look for IDs with 4+ digits
                r'data-region-id="(\d+)"',
                r'regionId.*?(\d{4,})'
            ]
            
            for pattern in id_patterns:
                matches = re.findall(pattern, html_content)
                if matches:
                    # Use the first reasonable-looking ID
                    for match in matches:
                        region_id = int(match)
                        if 1000 <= region_id <= 999999:  # Reasonable range for region IDs
                            return {
                                'region_id': region_id,
                                'region_type': region_type,
                                'region_name': f"{city}, {state}",
                                'city': city,
                                'state': state
                            }
            
            logger.warning(f"Could not extract region ID from page for {city}, {state}")
            return None
            
        except Exception as e:
            logger.error(f"Error extracting region info: {str(e)}")
            return None
    
    def _search_properties_modern(self, region_info: Dict, min_price: int, max_price: int, map_bounds: MapBounds, status: str, limit: int = 10) -> List[Property]:
        """Search properties using modern Zillow URL structure"""
        try:
            city_formatted = region_info['city'].lower().replace(' ', '-')
            state_formatted = region_info['state'].lower()
            base_url = f"https://www.zillow.com/{city_formatted}-{state_formatted}/"
            
            # Build the search query state
            search_query = {
                "pagination": {},
                "isMapVisible": True,
                "mapBounds": {
                    "west": map_bounds.west,
                    "east": map_bounds.east,
                    "south": map_bounds.south,
                    "north": map_bounds.north
                },
                "filterState": {
                    "sort": {"value": "globalrelevanceex"},
                    "price": {"min": min_price, "max": max_price}
                },
                "isListVisible": True,
                "mapZoom": 11,
                "usersSearchTerm": f"{region_info['city']} {region_info['state']}",
                "listPriceActive": True
            }
            
            # Add region selection if we have a valid region ID
            if region_info.get('region_id', 0) > 0:
                search_query["regionSelection"] = [{
                    "regionId": region_info['region_id'],
                    "regionType": region_info.get('region_type', 6)
                }]
            
            # Modify filter state based on status
            if status == "sold":
                # Add sold filter
                search_query["filterState"]["rs"] = {"value": "SOLD"}
                search_query["filterState"]["sort"] = {"value": "globalrelevanceex"}
                search_query["listPriceActive"] = False
            
            # Encode the search query
            search_query_encoded = quote(json.dumps(search_query, separators=(',', ':')))
            search_url = f"{base_url}?searchQueryState={search_query_encoded}"
            
            logger.info(f"Modern search URL: {search_url[:200]}...")  # Log first 200 chars
            
            time.sleep(self.request_delay)
            response = self.session.get(search_url, timeout=15)
            
            if response.status_code != 200:
                logger.warning(f"Search request failed with status {response.status_code}")
                return []
            
            properties = self._parse_modern_search_results(response.text, status)
            logger.info(f"Parsed {len(properties)} properties from modern search results")
            
            return properties[:limit]
            
        except Exception as e:
            logger.error(f"Error in modern property search: {str(e)}")
            return []
    
    def _parse_modern_search_results(self, html_content: str, status: str) -> List[Property]:
        """Parse property listings from modern Zillow search results"""
        try:
            properties = []
            
            # Method 1: Look for JSON data in script tags
            properties.extend(self._extract_from_json_scripts(html_content, status))
            
            # Method 2: Parse HTML elements if JSON parsing fails
            if not properties:
                properties.extend(self._parse_html_property_cards(html_content, status))
            
            return properties
            
        except Exception as e:
            logger.error(f"Error parsing modern search results: {str(e)}")
            return []
    
    def _extract_from_json_scripts(self, html_content: str, status: str) -> List[Property]:
        """Extract properties from JSON data in script tags"""
        properties = []
        
        try:
            # Look for common JSON patterns in Zillow pages
            json_patterns = [
                r'"listResults":\s*(\[.*?\])',
                r'"searchResults":\s*(\[.*?\])',
                r'"props":\s*({.*?"searchResults".*?})',
                r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                r'{"listResults":\s*(\[.*?\])'
            ]
            
            for pattern in json_patterns:
                matches = re.finditer(pattern, html_content, re.DOTALL)
                for match in matches:
                    try:
                        json_str = match.group(1) if len(match.groups()) == 1 else match.group(0)
                        
                        # Try to parse as JSON
                        if json_str.startswith('['):
                            # It's an array of listings
                            listings = json.loads(json_str)
                        elif json_str.startswith('{'):
                            # It's an object, look for listings inside
                            data = json.loads(json_str)
                            listings = self._find_listings_in_json(data)
                        else:
                            continue
                        
                        # Process each listing
                        for listing in listings:
                            if isinstance(listing, dict):
                                prop = self._create_property_from_json(listing, status)
                                if prop and prop.price > 0:
                                    properties.append(prop)
                        
                        if properties:  # If we found properties, stop looking
                            break
                            
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        logger.debug(f"Error processing JSON match: {str(e)}")
                        continue
                
                if properties:  # If we found properties, stop trying other patterns
                    break
        
        except Exception as e:
            logger.error(f"Error extracting from JSON scripts: {str(e)}")
        
        return properties
    
    def _find_listings_in_json(self, data: Dict) -> List[Dict]:
        """Recursively find property listings in nested JSON data"""
        listings = []
        
        if isinstance(data, dict):
            # Common keys that contain property listings
            listing_keys = ['listResults', 'searchResults', 'results', 'listings', 'properties']
            
            for key in listing_keys:
                if key in data and isinstance(data[key], list):
                    listings.extend(data[key])
            
            # Recursively search in nested objects
            for value in data.values():
                if isinstance(value, (dict, list)):
                    listings.extend(self._find_listings_in_json(value))
        
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    # Check if this item looks like a property listing
                    if any(key in item for key in ['address', 'price', 'beds', 'baths', 'zpid']):
                        listings.append(item)
                    else:
                        # Recursively search in nested items
                        listings.extend(self._find_listings_in_json(item))
        
        return listings
    
    def _parse_html_property_cards(self, html_content: str, status: str) -> List[Property]:
        """Parse property cards from HTML when JSON parsing fails"""
        properties = []
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Modern Zillow selectors
            property_selectors = [
                'article[data-test="property-card"]',
                '[data-test="property-card"]',
                '.PropertyCard',
                '.result-list-container article',
                '.property-card',
                '.ListItem',
                '[role="presentation"]'
            ]
            
            for selector in property_selectors:
                cards = soup.select(selector)
                if cards:
                    logger.info(f"Found {len(cards)} property cards using selector: {selector}")
                    for card in cards:
                        prop = self._extract_property_from_modern_card(card, status)
                        if prop and prop.price > 0:
                            properties.append(prop)
                    break
        
        except Exception as e:
            logger.error(f"Error parsing HTML property cards: {str(e)}")
        
        return properties
    
    def _extract_property_from_modern_card(self, card, status: str) -> Optional[Property]:
        """Extract property information from modern property card"""
        try:
            # Modern price selectors
            price_selectors = [
                '[data-test="property-card-price"]',
                '.PropertyCardWrapper__StyledPriceLine',
                '[data-testid="price"]',
                '.price',
                '.Text-c11n-8-99-0__sc-aiai24-0'
            ]
            
            price = 0
            for selector in price_selectors:
                price_elem = card.select_one(selector)
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    price = self._parse_price(price_text)
                    if price > 0:
                        break
            
            # Modern address selectors
            address_selectors = [
                '[data-test="property-card-addr"]',
                '.PropertyCardWrapper__StyledPropertyCardDataWrapper address',
                '[data-testid="address"]',
                '.address',
                'address'
            ]
            
            address = "Address not available"
            for selector in address_selectors:
                addr_elem = card.select_one(selector)
                if addr_elem:
                    address = addr_elem.get_text(strip=True)
                    break
            
            # Modern details selectors
            details_selectors = [
                '[data-test="property-card-details"]',
                '.PropertyCardWrapper__StyledPropertyCardDataWrapper ul',
                '[data-testid="bed-bath-sqft"]',
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
                    if bedrooms > 0 or bathrooms > 0 or square_feet > 0:
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
            logger.error(f"Error extracting property from modern card: {str(e)}")
            return None
    
    def _create_property_from_json(self, listing_data: Dict, status: str) -> Optional[Property]:
        """Create Property object from JSON listing data"""
        try:
            # Extract address
            address = "Address not available"
            if 'address' in listing_data:
                address = listing_data['address']
            elif 'addressStreet' in listing_data:
                address = listing_data['addressStreet']
            elif 'fullAddress' in listing_data:
                address = listing_data['fullAddress']
            
            # Extract price
            price = 0
            price_fields = ['price', 'unformattedPrice', 'listPrice', 'soldPrice']
            for field in price_fields:
                if field in listing_data and listing_data[field]:
                    price = int(listing_data[field])
                    break
            
            # Extract property details
            bedrooms = listing_data.get('beds', listing_data.get('bedrooms', 0))
            bathrooms = listing_data.get('baths', listing_data.get('bathrooms', 0))
            square_feet = listing_data.get('area', listing_data.get('livingArea', listing_data.get('sqft', 0)))
            
            # Extract URL
            url = "https://www.zillow.com"
            if 'detailUrl' in listing_data:
                url = f"https://www.zillow.com{listing_data['detailUrl']}"
            elif 'url' in listing_data:
                url = listing_data['url']
            elif 'zpid' in listing_data:
                url = f"https://www.zillow.com/homedetails/{listing_data['zpid']}_zpid/"
            
            if price > 0:
                return Property(
                    address=address,
                    bedrooms=bedrooms if isinstance(bedrooms, int) else 0,
                    bathrooms=float(bathrooms) if bathrooms else 0,
                    square_feet=square_feet if isinstance(square_feet, int) else 0,
                    price=price,
                    url=url,
                    status=status,
                    sold_date=listing_data.get('dateSold') if status == 'sold' else None
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error creating property from JSON: {str(e)}")
            return None
    
    def _parse_price(self, price_text: str) -> int:
        """Parse price from text"""
        if not price_text:
            return 0
        
        # Remove non-numeric characters except commas
        price_clean = re.sub(r'[^\d,]', '', price_text)
        
        if price_clean:
            try:
                return int(price_clean.replace(',', ''))
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
app = FastAPI(title="Modern Zillow Real Estate API", version="3.0.0")

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
    region_info: Optional[Dict] = None

@app.get("/")
async def root():
    return {"message": "Modern Zillow Real Estate API", "version": "3.0.0"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

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
    """Search for subject property and comparable properties"""
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
    """Search for subject property and comparable properties (POST method)"""
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
            request.city, 
            request.state, 
            request.min_price, 
            request.max_price, 
            map_bounds
        )
        return PropertySearchResponse(**results)
    except Exception as e:
        logger.error(f"Search POST endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cities")
async def get_supported_cities():
    """Get list of cities with predefined map bounds"""
    return {
        "supported_cities": list(zillow_api.city_bounds.keys()),
        "note": "These cities have predefined map bounds for better search accuracy"
    }

@app.get("/region-info/{city}/{state}")
async def get_region_info(city: str, state: str):
    """Get region information for a specific city and state"""
    try:
        region_info = zillow_api._get_region_info(city, state)
        if region_info:
            return {"region_info": region_info}
        else:
            raise HTTPException(status_code=404, detail=f"Region information not found for {city}, {state}")
    except Exception as e:
        logger.error(f"Region info endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Error handler for validation errors
@app.exception_handler(422)
async def validation_exception_handler(request, exc):
    return {"error": "Validation Error", "details": str(exc)}

# Error handler for general exceptions
@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {str(exc)}")
    return {"error": "Internal Server Error", "message": "An unexpected error occurred"}

if __name__ == "__main__":
    # Get port from environment variable (Railway sets this automatically)
    port = int(os.environ.get("PORT", 8000))
    
    # Configure uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )
