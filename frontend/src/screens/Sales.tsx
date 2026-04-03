"use client"

import type React from "react"
import { useState, useEffect } from "react"
import { useAuth } from "../contexts/AuthContext"
import api from "../services/api"
import toast from "react-hot-toast"
import { Plus, Minus, ShoppingCart, DollarSign, Smartphone } from "lucide-react"

interface Product {
  id: number
  name: string
  barcode: string
  unit_price: number
  current_stock: number
}

interface CartItem {
  product: Product
  quantity: number
  unit_price: number
  total_price: number
}

interface Shift {
  id: number
  start_time: string
  opening_cash: number
}

const Sales: React.FC = () => {
  const { user } = useAuth()
  const [products, setProducts] = useState<Product[]>([])
  const [cart, setCart] = useState<CartItem[]>([])
  const [searchTerm, setSearchTerm] = useState("")
  const [paymentMethod, setPaymentMethod] = useState<"cash" | "mpesa" | "mixed">("cash")
  const [cashAmount, setCashAmount] = useState("")
  const [mpesaAmount, setMpesaAmount] = useState("")
  const [customerName, setCustomerName] = useState("")
  const [customerPhone, setCustomerPhone] = useState("")
  const [loading, setLoading] = useState(false)
  const [activeShift, setActiveShift] = useState<Shift | null>(null)
  const [showShiftModal, setShowShiftModal] = useState(false)
  const [openingCash, setOpeningCash] = useState("")

  useEffect(() => {
    fetchProducts()
    checkActiveShift()
  }, [])

  const fetchProducts = async () => {
    try {
      const response = await api.get("/products/")
      setProducts(response.data.results || response.data)
    } catch (error) {
      toast.error("Error fetching products")
    }
  }

  const checkActiveShift = async () => {
    try {
      const response = await api.get("/sales/shifts/current/")
      setActiveShift(response.data)
    } catch (error) {
      // No active shift
      if (!user?.is_active_shift) {
        setShowShiftModal(true)
      }
    }
  }

  const startShift = async () => {
    try {
      await api.post("/sales/shifts/start/", {
        opening_cash: Number.parseFloat(openingCash) || 0,
      })
      toast.success("Shift started successfully")
      setShowShiftModal(false)
      checkActiveShift()
    } catch (error) {
      toast.error("Error starting shift")
    }
  }

  const addToCart = (product: Product) => {
    if (product.current_stock <= 0) {
      toast.error("Product out of stock")
      return
    }

    const existingItem = cart.find((item) => item.product.id === product.id)
    if (existingItem) {
      if (existingItem.quantity >= product.current_stock) {
        toast.error("Insufficient stock")
        return
      }
      updateQuantity(product.id, existingItem.quantity + 1)
    } else {
      const newItem: CartItem = {
        product,
        quantity: 1,
        unit_price: product.unit_price,
        total_price: product.unit_price,
      }
      setCart([...cart, newItem])
    }
  }

  const updateQuantity = (productId: number, newQuantity: number) => {
    if (newQuantity <= 0) {
      removeFromCart(productId)
      return
    }

    const product = products.find((p) => p.id === productId)
    if (product && newQuantity > product.current_stock) {
      toast.error("Insufficient stock")
      return
    }

    setCart(
      cart.map((item) =>
        item.product.id === productId
          ? { ...item, quantity: newQuantity, total_price: newQuantity * item.unit_price }
          : item,
      ),
    )
  }

  const removeFromCart = (productId: number) => {
    setCart(cart.filter((item) => item.product.id !== productId))
  }

  const getCartTotal = () => {
    return cart.reduce((total, item) => total + item.total_price, 0)
  }

  const processSale = async () => {
    if (cart.length === 0) {
      toast.error("Cart is empty")
      return
    }

    if (!activeShift) {
      toast.error("No active shift. Please start a shift first.")
      return
    }

    const total = getCartTotal()
    let cashAmt = 0
    let mpesaAmt = 0

    if (paymentMethod === "cash") {
      cashAmt = total
    } else if (paymentMethod === "mpesa") {
      mpesaAmt = total
    } else {
      cashAmt = Number.parseFloat(cashAmount) || 0
      mpesaAmt = Number.parseFloat(mpesaAmount) || 0
      if (cashAmt + mpesaAmt !== total) {
        toast.error("Payment amounts do not match total")
        return
      }
    }

    setLoading(true)
    try {
      const saleData = {
        payment_method: paymentMethod,
        cash_amount: cashAmt,
        mpesa_amount: mpesaAmt,
        total_amount: total,
        customer_name: customerName,
        customer_phone: customerPhone,
        items: cart.map((item) => ({
          product_id: item.product.id,
          quantity: item.quantity,
          unit_price: item.unit_price,
        })),
      }

      const response = await api.post("/sales/process/", saleData)
      toast.success(`Sale processed successfully! Receipt: ${response.data.receipt_number}`)

      // Reset form
      setCart([])
      setCashAmount("")
      setMpesaAmount("")
      setCustomerName("")
      setCustomerPhone("")
      setPaymentMethod("cash")

      // Refresh products to update stock
      fetchProducts()
    } catch (error) {
      toast.error("Error processing sale")
    } finally {
      setLoading(false)
    }
  }

  const filteredProducts = products.filter(
    (product) => product.name.toLowerCase().includes(searchTerm.toLowerCase()) || product.barcode.includes(searchTerm),
  )

  if (showShiftModal) {
    return (
      <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center">
        <div className="bg-white p-6 rounded-lg shadow-xl max-w-md w-full">
          <h2 className="text-xl font-bold mb-4">Start New Shift</h2>
          <p className="text-gray-600 mb-4">You need to start a shift before making sales.</p>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">Opening Cash Amount (KES)</label>
            <input
              type="number"
              value={openingCash}
              onChange={(e) => setOpeningCash(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="0.00"
            />
          </div>
          <div className="flex space-x-3">
            <button
              onClick={startShift}
              className="flex-1 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700"
            >
              Start Shift
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Products Section */}
      <div className="lg:col-span-2">
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Products</h3>

            {/* Search */}
            <div className="mb-4">
              <input
                type="text"
                placeholder="Search products by name or barcode..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* Products Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-h-96 overflow-y-auto">
              {filteredProducts.map((product) => (
                <div
                  key={product.id}
                  className="border border-gray-200 rounded-lg p-4 hover:shadow-md cursor-pointer"
                  onClick={() => addToCart(product)}
                >
                  <h4 className="font-medium text-gray-900">{product.name}</h4>
                  <p className="text-sm text-gray-500">Stock: {product.current_stock}</p>
                  <p className="text-lg font-bold text-blue-600">KES {product.unit_price.toFixed(2)}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Cart and Payment Section */}
      <div className="space-y-6">
        {/* Cart */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4 flex items-center">
              <ShoppingCart className="mr-2 h-5 w-5" />
              Cart ({cart.length})
            </h3>

            <div className="space-y-3 max-h-64 overflow-y-auto">
              {cart.map((item) => (
                <div key={item.product.id} className="flex items-center justify-between border-b pb-2">
                  <div className="flex-1">
                    <p className="font-medium text-sm">{item.product.name}</p>
                    <p className="text-sm text-gray-500">KES {item.unit_price.toFixed(2)} each</p>
                  </div>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => updateQuantity(item.product.id, item.quantity - 1)}
                      className="p-1 text-gray-400 hover:text-gray-600"
                    >
                      <Minus className="h-4 w-4" />
                    </button>
                    <span className="w-8 text-center">{item.quantity}</span>
                    <button
                      onClick={() => updateQuantity(item.product.id, item.quantity + 1)}
                      className="p-1 text-gray-400 hover:text-gray-600"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="ml-4 text-right">
                    <p className="font-medium">KES {item.total_price.toFixed(2)}</p>
                  </div>
                </div>
              ))}
            </div>

            {cart.length > 0 && (
              <div className="mt-4 pt-4 border-t">
                <div className="flex justify-between text-lg font-bold">
                  <span>Total:</span>
                  <span>KES {getCartTotal().toFixed(2)}</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Payment */}
        {cart.length > 0 && (
          <div className="bg-white shadow rounded-lg">
            <div className="px-4 py-5 sm:p-6">
              <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Payment</h3>

              {/* Customer Info */}
              <div className="space-y-3 mb-4">
                <input
                  type="text"
                  placeholder="Customer Name (Optional)"
                  value={customerName}
                  onChange={(e) => setCustomerName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <input
                  type="tel"
                  placeholder="Customer Phone (Optional)"
                  value={customerPhone}
                  onChange={(e) => setCustomerPhone(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              {/* Payment Method */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-2">Payment Method</label>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    onClick={() => setPaymentMethod("cash")}
                    className={`p-2 text-sm rounded-md border ${
                      paymentMethod === "cash"
                        ? "bg-blue-50 border-blue-500 text-blue-700"
                        : "border-gray-300 text-gray-700"
                    }`}
                  >
                    <DollarSign className="h-4 w-4 mx-auto mb-1" />
                    Cash
                  </button>
                  <button
                    onClick={() => setPaymentMethod("mpesa")}
                    className={`p-2 text-sm rounded-md border ${
                      paymentMethod === "mpesa"
                        ? "bg-green-50 border-green-500 text-green-700"
                        : "border-gray-300 text-gray-700"
                    }`}
                  >
                    <Smartphone className="h-4 w-4 mx-auto mb-1" />
                    M-Pesa
                  </button>
                  <button
                    onClick={() => setPaymentMethod("mixed")}
                    className={`p-2 text-sm rounded-md border ${
                      paymentMethod === "mixed"
                        ? "bg-purple-50 border-purple-500 text-purple-700"
                        : "border-gray-300 text-gray-700"
                    }`}
                  >
                    Mixed
                  </button>
                </div>
              </div>

              {/* Payment Amounts */}
              {paymentMethod === "mixed" && (
                <div className="space-y-3 mb-4">
                  <input
                    type="number"
                    placeholder="Cash Amount"
                    value={cashAmount}
                    onChange={(e) => setCashAmount(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <input
                    type="number"
                    placeholder="M-Pesa Amount"
                    value={mpesaAmount}
                    onChange={(e) => setMpesaAmount(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              )}

              <button
                onClick={processSale}
                disabled={loading}
                className="w-full bg-blue-600 text-white py-3 px-4 rounded-md hover:bg-blue-700 disabled:opacity-50 font-medium"
              >
                {loading ? "Processing..." : `Process Sale - KES ${getCartTotal().toFixed(2)}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default Sales
